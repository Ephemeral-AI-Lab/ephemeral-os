//! The composition-root dependency graph ([`AppState`]) and its
//! [`AppStateBuilder`].
//!
//! Every store and seam is constructed exactly once here (GC-eos-runtime-02):
//! there are no module-level mutable singletons. `AppState` is a cheap-to-clone
//! handle (`Arc` fields) shared into each spawned agent and delegated workflow.

use std::path::PathBuf;
use std::sync::{Arc, Mutex as StdMutex};

use anyhow::{Context, Result};
use async_trait::async_trait;
use eos_agent_def::{load_agents_tree, AgentRegistry, AgentRegistryBuilder};
use eos_audit::{AuditSink, BufferedAuditShutdown, BufferedJsonlSink, NoopAuditSink};
use eos_config::{
    DatabaseConfig, DatabaseUrl, ProviderKind, ProvidersConfig, SecretConfigValue, WorkflowConfig,
};
use eos_db::Database;
use eos_llm_client::{Auth, LlmClient, LlmRequest, LlmStream, ProviderError};
use eos_sandbox_api::SandboxTransport;
use eos_sandbox_host::{
    resolve_provider_kind, DaemonClient, DockerProviderAdapter, ProviderRegistry,
    RequestSandboxBinding, RequestSandboxProvisioner, SandboxLifecycle,
};
use eos_skills::SkillRegistry;
use eos_state::{
    AgentRunStore, AttemptStore, IterationStore, ModelStore, RequestStore, TaskStore, WorkflowStore,
};
use eos_tools::{
    build_default_registry, CallerScope, IsolatedWorkspacePort, ToolConfigSet, ToolKey,
    ToolRegistry,
};
use eos_types::RequestId;

// The per-agent event-source factory and per-run stream-event callback are owned
// by `eos-engine` (next to the loop they drive, so the engine-driven advisor run
// can resolve a source without a runtime back-edge) and re-exported here for the
// composition root and the `run_request` signature.
pub use eos_engine::{EventCallback, EventSourceFactory};

use crate::isolated_workspace::RuntimeIsolatedWorkspace;
use crate::plugin_tools::register_plugin_tools;

/// Request-scoped sandbox provisioning seam.
///
/// `eos-sandbox-host` owns the production [`RequestSandboxProvisioner`] over the
/// sealed `ProviderAdapter`/`SandboxLifecycle` seam (a parallel agent moved the
/// work there). Because that adapter is sealed, `eos-runtime` cannot build a mock
/// of it, so this narrow runtime seam exists purely for testability: production
/// wraps the host provisioner; tests inject a fake.
#[async_trait]
pub(crate) trait RequestProvisioner: Send + Sync + std::fmt::Debug {
    /// Resolve the sandbox binding for one request (start an explicit id, or
    /// create a fresh `request-<hex8>` sandbox labelled `origin=workflow`).
    async fn prepare_for_run(
        &self,
        request_id: &RequestId,
        sandbox_id: Option<&str>,
    ) -> Result<RequestSandboxBinding>;
}

/// Production provisioner: wraps the `eos-sandbox-host` provisioner over the real
/// container lifecycle.
#[derive(Debug)]
struct HostProvisioner {
    inner: Arc<RequestSandboxProvisioner>,
}

#[async_trait]
impl RequestProvisioner for HostProvisioner {
    async fn prepare_for_run(
        &self,
        request_id: &RequestId,
        sandbox_id: Option<&str>,
    ) -> Result<RequestSandboxBinding> {
        self.inner
            .prepare_for_run(request_id, sandbox_id)
            .await
            .context("sandbox provisioning failed")
    }
}

/// Placeholder client used when no provider is selected and no
/// `event_source_factory` is set. Streaming always errors; production wires a
/// real provider from `providers.active`, and tests set `event_source_factory`.
#[derive(Debug, Default)]
struct UnconfiguredLlmClient;

#[async_trait]
impl LlmClient for UnconfiguredLlmClient {
    async fn stream_message(&self, _request: LlmRequest) -> Result<LlmStream, ProviderError> {
        Err(ProviderError::transport(
            "no llm provider configured (set providers.active in local.yml or inject an event_source_factory)",
        ))
    }
}

/// The composition-root dependency graph. Cloning is cheap (every field is an
/// `Arc` or `Clone`-internal handle).
#[derive(Clone)]
#[non_exhaustive]
pub struct AppState {
    pub(crate) workflow: WorkflowConfig,
    pub(crate) cwd: String,
    pub(crate) repo_root: String,
    pub(crate) task_store: Arc<dyn TaskStore>,
    pub(crate) request_store: Arc<dyn RequestStore>,
    pub(crate) workflow_store: Arc<dyn WorkflowStore>,
    pub(crate) iteration_store: Arc<dyn IterationStore>,
    pub(crate) attempt_store: Arc<dyn AttemptStore>,
    pub(crate) agent_run_store: Arc<dyn AgentRunStore>,
    pub(crate) model_store: Arc<dyn ModelStore>,
    pub(crate) llm_client: Arc<dyn LlmClient>,
    pub(crate) event_source_factory: Option<EventSourceFactory>,
    pub(crate) audit: Arc<dyn AuditSink>,
    pub(crate) audit_shutdown: Arc<StdMutex<Option<BufferedAuditShutdown>>>,
    pub(crate) tool_config: Arc<ToolConfigSet>,
    pub(crate) agent_registry: Arc<AgentRegistry>,
    pub(crate) skill_registry: Arc<SkillRegistry>,
    pub(crate) transport: Arc<dyn SandboxTransport>,
    pub(crate) isolated_workspace: Arc<dyn IsolatedWorkspacePort>,
    pub(crate) provisioner: Arc<dyn RequestProvisioner>,
}

impl std::fmt::Debug for AppState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AppState")
            .field("cwd", &self.cwd)
            .field("repo_root", &self.repo_root)
            .field("agents", &self.agent_registry.list().count())
            .finish_non_exhaustive()
    }
}

impl AppState {
    /// Start building an `AppState`.
    pub fn builder() -> AppStateBuilder {
        AppStateBuilder::default()
    }

    /// Bundle the explicit run handles `eos_engine::run_agent` needs (in
    /// place of `&AppState`, advisor remediation plan §6). Cheap (Arc/`String`
    /// clones); the root-agent and delegated-workflow runners pass this in.
    pub(crate) fn engine_run_handles(&self) -> eos_engine::EngineRunHandles {
        eos_engine::EngineRunHandles {
            agent_run_store: self.agent_run_store.clone(),
            model_store: self.model_store.clone(),
            llm_client: self.llm_client.clone(),
            event_source_factory: self.event_source_factory.clone(),
            agent_registry: self.agent_registry.clone(),
            tool_config: self.tool_config.clone(),
            tool_registry_extender: Some(Arc::new(register_plugin_tools)),
            audit: self.audit.clone(),
            cwd: self.cwd.clone(),
        }
    }

    /// Flush and join the buffered audit writer thread, if any (graceful
    /// shutdown). Idempotent: a second call is a no-op.
    pub fn flush_audit(&self) {
        if let Ok(mut guard) = self.audit_shutdown.lock() {
            if let Some(shutdown) = guard.take() {
                shutdown.shutdown();
            }
        }
    }
}

/// `#[must_use]` builder for [`AppState`]. Every field is an optional override:
/// `None` selects the production default. Tests inject in-memory stores, a mock
/// `event_source_factory`, a fake provisioner, and explicit registries.
#[must_use = "AppStateBuilder does nothing until build() is called"]
#[derive(Default)]
pub struct AppStateBuilder {
    database_url: Option<String>,
    cwd: Option<String>,
    llm_client: Option<Arc<dyn LlmClient>>,
    providers_config: Option<ProvidersConfig>,
    workflow_config: Option<WorkflowConfig>,
    event_source_factory: Option<EventSourceFactory>,
    audit: Option<Arc<dyn AuditSink>>,
    audit_path: Option<PathBuf>,
    agent_registry: Option<Arc<AgentRegistry>>,
    agents_dir: Option<PathBuf>,
    tool_config: Option<Arc<ToolConfigSet>>,
    tools_root: Option<PathBuf>,
    skill_registry: Option<Arc<SkillRegistry>>,
    skill_root: Option<PathBuf>,
    model_registry_path: Option<PathBuf>,
    provisioner: Option<Arc<dyn RequestProvisioner>>,
    transport: Option<Arc<dyn SandboxTransport>>,
    compatibility_mode: bool,
}

impl std::fmt::Debug for AppStateBuilder {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AppStateBuilder")
            .field("compatibility_mode", &self.compatibility_mode)
            .finish_non_exhaustive()
    }
}

impl AppStateBuilder {
    /// Override the database URL (test seam; a network URL makes `build()` fail
    /// fast).
    pub fn database_url(mut self, url: impl Into<String>) -> Self {
        self.database_url = Some(url.into());
        self
    }

    /// Set the working directory (process cwd by default).
    pub fn cwd(mut self, cwd: impl Into<String>) -> Self {
        self.cwd = Some(cwd.into());
        self
    }

    /// Inject an LLM client (an unconfigured placeholder by default).
    pub fn llm_client(mut self, client: Arc<dyn LlmClient>) -> Self {
        self.llm_client = Some(client);
        self
    }

    /// Inject provider config used to construct the default LLM client.
    pub fn providers_config(mut self, config: ProvidersConfig) -> Self {
        self.providers_config = Some(config);
        self
    }

    /// Inject workflow config used to wire attempt and planner-depth runtime
    /// tunables.
    pub fn workflow_config(mut self, config: WorkflowConfig) -> Self {
        self.workflow_config = Some(config);
        self
    }

    /// Inject the per-agent event-source factory (mock harness).
    pub fn event_source_factory(mut self, factory: EventSourceFactory) -> Self {
        self.event_source_factory = Some(factory);
        self
    }

    /// Inject an audit sink (a no-op sink by default unless `audit_path` is set).
    pub fn audit(mut self, audit: Arc<dyn AuditSink>) -> Self {
        self.audit = Some(audit);
        self
    }

    /// Write audit events to a buffered JSONL file at `path`.
    pub fn audit_path(mut self, path: impl Into<PathBuf>) -> Self {
        self.audit_path = Some(path.into());
        self
    }

    /// Inject a prebuilt agent registry (else load from `agents_dir`, else empty).
    pub fn agent_registry(mut self, registry: Arc<AgentRegistry>) -> Self {
        self.agent_registry = Some(registry);
        self
    }

    /// Load agent profiles from this directory tree.
    pub fn agents_dir(mut self, dir: impl Into<PathBuf>) -> Self {
        self.agents_dir = Some(dir.into());
        self
    }

    /// Inject a prebuilt tool config (else load from `tools_root`).
    pub fn tool_config(mut self, config: Arc<ToolConfigSet>) -> Self {
        self.tool_config = Some(config);
        self
    }

    /// Load the externalized tool config from this `.eos-agents/tools` root.
    pub fn tools_root(mut self, root: impl Into<PathBuf>) -> Self {
        self.tools_root = Some(root.into());
        self
    }

    /// Inject a prebuilt skill registry (else load from `skill_root`, else empty).
    pub fn skill_registry(mut self, registry: Arc<SkillRegistry>) -> Self {
        self.skill_registry = Some(registry);
        self
    }

    /// Load skills from this root directory.
    pub fn skill_root(mut self, root: impl Into<PathBuf>) -> Self {
        self.skill_root = Some(root.into());
        self
    }

    /// Seed the model registry from this JSON file (missing file is non-fatal).
    pub fn model_registry_path(mut self, path: impl Into<PathBuf>) -> Self {
        self.model_registry_path = Some(path.into());
        self
    }

    /// Inject a request provisioner (a host-backed provisioner by default).
    #[cfg(test)]
    pub(crate) fn provisioner(mut self, provisioner: Arc<dyn RequestProvisioner>) -> Self {
        self.provisioner = Some(provisioner);
        self
    }

    /// Inject the sandbox transport (a daemon client over the provider registry
    /// by default). Tests inject a fake transport to avoid a live daemon.
    pub fn transport(mut self, transport: Arc<dyn SandboxTransport>) -> Self {
        self.transport = Some(transport);
        self
    }

    /// Allow agent profiles to name tools absent from the registry (skip-unknown
    /// compatibility instead of failing startup).
    pub fn compatibility_mode(mut self, enabled: bool) -> Self {
        self.compatibility_mode = enabled;
        self
    }

    /// Construct the runtime graph: build the `SQLite` pool (fail fast on a network
    /// URL), construct every store and seam, optionally seed the model registry,
    /// and validate agent profile tool names against the registry.
    ///
    /// # Errors
    /// Returns an error if the DB URL is non-local, the pool/migrations fail, a
    /// configured agent/skill/plugin root cannot be loaded, or (without
    /// compatibility mode) an agent names an unknown tool.
    pub async fn build(self) -> Result<AppState> {
        // Database: a network URL fails fast at parse (GC: SQLite-only).
        // `DatabaseConfig` is `#[non_exhaustive]`, so override the url by mutation
        // rather than struct-update syntax.
        let mut db_config = DatabaseConfig::default();
        if let Some(url) = self.database_url {
            db_config.url =
                DatabaseUrl::parse(url).context("database url is not a local sqlite url")?;
        }
        let database = Database::open(&db_config)
            .await
            .context("opening the sqlite database")?;

        let cwd = self
            .cwd
            .or_else(|| {
                std::env::current_dir()
                    .ok()
                    .map(|p| p.display().to_string())
            })
            .unwrap_or_default();
        let repo_root = cwd.clone();

        // Optional model-registry seed (GC-eos-runtime-04: missing JSON is
        // non-fatal — seed_from_json returns Ok(0) for a missing file).
        let model_path = self.model_registry_path.or_else(|| {
            let candidate = PathBuf::from(&repo_root)
                .join("models")
                .join("registry.json");
            candidate.is_file().then_some(candidate)
        });
        if let Some(path) = &model_path {
            match database
                .model_registry()
                .seed_from_json(&path.display().to_string())
                .await
            {
                Ok(count) => tracing::info!(models = count, "seeded model registry"),
                Err(err) => {
                    tracing::warn!(error = %err, "model registry seed skipped (non-fatal)");
                }
            }
        }

        let config_doc = eos_config::load().context("loading runtime config")?;
        let workflow_config = match self.workflow_config {
            Some(config) => config,
            None => config_doc
                .section::<WorkflowConfig>("workflow")
                .context("loading workflow config")?,
        };
        workflow_config
            .validate()
            .context("validating workflow config")?;

        let llm_client: Arc<dyn LlmClient> = match self.llm_client {
            Some(client) => client,
            None => {
                let providers_config = match self.providers_config {
                    Some(config) => config,
                    None => config_doc
                        .section::<ProvidersConfig>("providers")
                        .context("loading providers config")?,
                };
                providers_config
                    .validate()
                    .context("validating providers config")?;
                default_llm_client(&providers_config).context("configuring llm provider")?
            }
        };

        // Audit: explicit sink wins; else a buffered JSONL sink when a path is
        // configured; else a no-op sink.
        let (audit, audit_shutdown): (Arc<dyn AuditSink>, Option<BufferedAuditShutdown>) =
            match (self.audit, &self.audit_path) {
                (Some(sink), _) => (sink, None),
                (None, Some(path)) => {
                    let (sink, shutdown) = BufferedJsonlSink::new(path.clone(), 1024)
                        .context("opening the audit jsonl sink")?;
                    (Arc::new(sink), Some(shutdown))
                }
                (None, None) => (Arc::new(NoopAuditSink), None),
            };

        let agent_registry = match self.agent_registry {
            Some(registry) => registry,
            None => Arc::new(build_agent_registry(self.agents_dir.as_deref())?),
        };

        let skill_registry = match self.skill_registry {
            Some(registry) => registry,
            None => Arc::new(build_skill_registry(self.skill_root.as_deref())?),
        };

        let tool_config = match self.tool_config {
            Some(config) => Arc::new(
                (*config)
                    .clone()
                    .with_workflow_max_depth(workflow_config.max_depth),
            ),
            None => Arc::new(
                build_tool_config(self.tools_root.as_deref())?
                    .with_workflow_max_depth(workflow_config.max_depth),
            ),
        };

        let mut tool_registry = build_default_registry(&tool_config, &CallerScope::default());
        register_plugin_tools(&mut tool_registry);

        // Cross-registry validation: unknown agent tool names fail fast unless
        // compatibility mode is enabled (anchor §10 / AC-eos-runtime-09).
        if !self.compatibility_mode {
            validate_agent_tools(&agent_registry, &tool_registry)?;
        }

        let needs_host_provider = self.transport.is_none() || self.provisioner.is_none();
        let provider_registry = Arc::new(ProviderRegistry::new());
        if needs_host_provider {
            seed_default_sandbox_provider(&provider_registry)?;
        }
        let daemon_client = Arc::new(DaemonClient::new(provider_registry));
        let transport: Arc<dyn SandboxTransport> =
            self.transport.unwrap_or_else(|| daemon_client.clone());
        let isolated_workspace: Arc<dyn IsolatedWorkspacePort> =
            Arc::new(RuntimeIsolatedWorkspace::new(transport.clone()));
        let eosd_artifact_dir = default_eosd_artifact_dir(&repo_root);

        let provisioner: Arc<dyn RequestProvisioner> = self.provisioner.unwrap_or_else(|| {
            let lifecycle = SandboxLifecycle::new(daemon_client.clone(), eosd_artifact_dir);
            Arc::new(HostProvisioner {
                inner: Arc::new(RequestSandboxProvisioner::with_default_snapshot(
                    Arc::new(lifecycle),
                    // No host-side default snapshot: a fresh sandbox uses the
                    // provider default unless a request supplies an explicit id.
                    None,
                )),
            })
        });

        Ok(AppState {
            workflow: workflow_config,
            cwd,
            repo_root,
            task_store: database.tasks(),
            request_store: database.requests(),
            workflow_store: database.workflows(),
            iteration_store: database.iterations(),
            attempt_store: database.attempts(),
            agent_run_store: database.agent_runs(),
            model_store: database.models(),
            llm_client,
            event_source_factory: self.event_source_factory,
            audit,
            audit_shutdown: Arc::new(StdMutex::new(audit_shutdown)),
            tool_config,
            agent_registry,
            skill_registry,
            transport,
            isolated_workspace,
            provisioner,
        })
    }
}

/// Build the LLM client from the loaded `providers` config.
fn default_llm_client(providers: &ProvidersConfig) -> Result<Arc<dyn LlmClient>, ProviderError> {
    use eos_llm_client::{
        AnthropicApiClient, ClaudeCodingPlanClient, CodexCodingPlanClient, OpenAiApiClient,
    };

    let retry = Arc::new(providers.retry.clone());
    match providers.active {
        ProviderKind::Unconfigured => Ok(Arc::new(UnconfiguredLlmClient)),
        ProviderKind::OpenAiApi => {
            let key = provider_secret(
                "providers.openai_api.api_key",
                providers.openai_api.api_key.as_ref(),
            )?;
            Ok(Arc::new(OpenAiApiClient::new(
                &providers.openai_api.base_url,
                Auth::bearer(key),
                retry,
            )?))
        }
        ProviderKind::AnthropicApi => {
            let key = provider_secret(
                "providers.anthropic_api.api_key",
                providers.anthropic_api.api_key.as_ref(),
            )?;
            Ok(Arc::new(AnthropicApiClient::new(
                &providers.anthropic_api.base_url,
                Auth::api_key(key),
                retry,
            )?))
        }
        ProviderKind::CodexCodingPlan => {
            let token = provider_secret(
                "providers.codex_coding_plan.access_token",
                providers.codex_coding_plan.access_token.as_ref(),
            )?;
            let auth = Auth::codex_access_token_from_jwt(token)?;
            Ok(Arc::new(CodexCodingPlanClient::new(
                &providers.codex_coding_plan.base_url,
                auth,
                retry,
            )?))
        }
        ProviderKind::ClaudeCodingPlan => {
            let token = provider_secret(
                "providers.claude_coding_plan.access_token",
                providers.claude_coding_plan.access_token.as_ref(),
            )?;
            Ok(Arc::new(ClaudeCodingPlanClient::new(
                &providers.claude_coding_plan.base_url,
                token,
                retry,
            )?))
        }
        _ => Err(ProviderError::request("unsupported providers.active value")),
    }
}

fn provider_secret<'a>(
    field: &str,
    value: Option<&'a SecretConfigValue>,
) -> Result<&'a str, ProviderError> {
    match value {
        Some(value) if !value.is_empty() => Ok(value.expose_secret()),
        _ => Err(ProviderError::request(format!("{field} is required"))),
    }
}

fn seed_default_sandbox_provider(registry: &ProviderRegistry) -> Result<()> {
    let provider_kind = resolve_provider_kind();
    let docker = DockerProviderAdapter::connect().context("connecting docker sandbox provider")?;
    registry.set_default(Arc::new(docker));
    tracing::info!(
        sandbox_provider = provider_kind.as_str(),
        "sandbox provider configured"
    );
    Ok(())
}

fn default_eosd_artifact_dir(repo_root: &str) -> PathBuf {
    PathBuf::from(repo_root).join("sandbox").join("dist")
}

fn build_agent_registry(dir: Option<&std::path::Path>) -> Result<AgentRegistry> {
    let Some(dir) = dir else {
        return Ok(AgentRegistryBuilder::new().build());
    };
    if !dir.is_dir() {
        return Ok(AgentRegistryBuilder::new().build());
    }
    let defs = load_agents_tree(dir).context("loading agent profiles")?;
    let mut builder = AgentRegistryBuilder::new();
    for def in defs {
        builder.add(def);
    }
    Ok(builder.build())
}

fn build_skill_registry(root: Option<&std::path::Path>) -> Result<SkillRegistry> {
    match root {
        Some(root) => SkillRegistry::load_from_dir(root).context("loading skills"),
        None => Ok(SkillRegistry::new()),
    }
}

/// Load the externalized tool config. Unlike skills/plugins, the tool config is
/// **mandatory** (the registry needs all tools), so a missing root is an error:
/// inject via [`AppStateBuilder::tool_config`] or point at a `.eos-agents/tools`
/// tree via [`AppStateBuilder::tools_root`].
fn build_tool_config(root: Option<&std::path::Path>) -> Result<ToolConfigSet> {
    let root = root
        .context("tool config root not set: call AppStateBuilder::tools_root or ::tool_config")?;
    ToolConfigSet::load_from_dir(root).context("loading tool config")
}

/// Validate that every `allowed_tools`/`terminals` entry on every agent profile
/// is a known, registered tool (AC-eos-runtime-09).
fn validate_agent_tools(agents: &AgentRegistry, registry: &ToolRegistry) -> Result<()> {
    for def in agents.list() {
        for tool in def.allowed_tools.iter().chain(def.terminals.iter()) {
            let known = ToolKey::from_wire(tool).is_some_and(|name| registry.get(name).is_some());
            if !known {
                anyhow::bail!(
                    "agent profile {:?} names unknown tool {:?}; enable compatibility mode to skip",
                    def.name.as_str(),
                    tool
                );
            }
        }
    }
    Ok(())
}

// Crate-local Layer-A fixtures (`build_test_state` + `FakeProvisioner`). They
// reference `eos-runtime` types, so the dev-dep two-instance rule bars consuming
// them from an external `eos-testkit` in this crate's own in-crate tests
// (`TESTING_SPEC` §14.2); the cross-crate-safe doubles still come from
// `eos-testkit`. Declared here (not under `tests`) so they can reach the
// `pub(crate)` provisioner seam via `super::`.
#[cfg(test)]
#[path = "../tests/unit/support.rs"]
pub(crate) mod support;

#[cfg(test)]
mod tests {
    // Pure-logic unit test for a module-private fn (no reusable doubles defined,
    // so I2-permitted inline). The shared Layer-A doubles moved to `eos-testkit`
    // (`TESTING_SPEC` §7); the behavior tests live in `tests/unit/mod.rs` and pull
    // those doubles from the `eos-testkit` dev-dep plus the local `support`
    // module.
    #[test]
    fn eosd_artifact_dir_is_repo_sandbox_dist() {
        assert_eq!(
            super::default_eosd_artifact_dir("/repo"),
            std::path::PathBuf::from("/repo/sandbox/dist")
        );
    }
}
