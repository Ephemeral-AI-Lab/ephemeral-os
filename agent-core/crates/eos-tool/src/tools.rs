//! Concrete model-callable tools and default registry construction.

pub(crate) mod ask_advisor;
pub(crate) mod command;
pub(crate) mod sandbox;
pub(crate) mod skills;
pub(crate) mod subagent;
pub(crate) mod submission;
pub mod terminal;
pub(crate) mod workflow;

use std::sync::Arc;

use async_trait::async_trait;
use eos_sandbox_port::{
    DaemonOp, SandboxCommandApi, SandboxCommandService, SandboxPortError, SandboxTransport,
};
use eos_types::{
    AgentRunApi, AgentRunError, AgentRunId, AgentRunOutcome, CommandSessionId, JsonObject,
    SandboxId, SpawnAgentRequest, StartedWorkflow, ToolSpec, WorkflowApi, WorkflowApiError,
    WorkflowId,
};
use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::registry::{
    BackgroundSessions, HookServices, Submission, ToolConfig, ToolConfigSet, ToolRuntime,
    WorkspaceMode,
};
use crate::{
    OutputShape, RegisteredTool, ToolError, ToolExecutor, ToolName, ToolRegistry, ToolResult,
};

pub use skills::{
    ReferenceName, SkillDefinition, SkillLoadError, SkillName, SkillRegistry, SkillSource,
};

/// The per-caller scope a tool registry is built for.
#[derive(Debug, Clone, Default)]
pub struct CallerScope {
    /// The subagent profile names this caller may dispatch.
    pub dispatchable_subagents: Vec<String>,
    /// The bound agent's own skill folder slug, if it declares one.
    pub skill_slug: Option<String>,
}

#[derive(Clone)]
pub(crate) struct SandboxHandle {
    pub(crate) transport: Arc<dyn SandboxTransport>,
    workspace_mode: Arc<dyn WorkspaceMode>,
}

impl SandboxHandle {
    fn new(runtime: &ToolRuntime) -> Self {
        Self {
            transport: runtime.sandbox.clone(),
            workspace_mode: runtime.workspace_mode.clone(),
        }
    }

    async fn set_isolated_workspace_mode(
        &self,
        agent_run_id: &AgentRunId,
        is_isolated: bool,
    ) -> Result<(), ToolError> {
        self.workspace_mode
            .set_isolated_workspace_mode(agent_run_id.clone(), is_isolated)
            .await
    }
}

#[derive(Clone)]
pub(crate) struct CommandHandle {
    pub(crate) command_service: Arc<dyn SandboxCommandApi>,
    background: Arc<dyn BackgroundSessions>,
}

impl CommandHandle {
    fn new(runtime: &ToolRuntime) -> Self {
        Self {
            command_service: Arc::new(SandboxCommandService::new(runtime.sandbox.clone())),
            background: runtime.background.clone(),
        }
    }

    async fn register_command(
        &self,
        command_session_id: &CommandSessionId,
        sandbox_id: &SandboxId,
    ) -> Result<(), ToolError> {
        self.background
            .register_command(command_session_id.clone(), sandbox_id.clone())
            .await
    }
}

#[derive(Clone)]
pub(crate) struct BackgroundHandle {
    background: Arc<dyn BackgroundSessions>,
}

impl BackgroundHandle {
    fn new(runtime: &ToolRuntime) -> Self {
        Self {
            background: runtime.background.clone(),
        }
    }

    async fn register_subagent(&self, agent_run_id: &AgentRunId) -> Result<(), ToolError> {
        self.background.register_subagent(agent_run_id.clone()).await
    }

    async fn cancel_subagent(&self, agent_run_id: &AgentRunId, reason: &str) -> Result<bool, ToolError> {
        self.background
            .cancel_subagent(agent_run_id.clone(), reason)
            .await
    }

    async fn register_workflow(&self, workflow: &StartedWorkflow) -> Result<(), ToolError> {
        self.background.register_workflow(workflow.clone()).await
    }
}

#[derive(Clone)]
pub(crate) struct RootSubmissionHandle {
    pub(crate) submission: Submission,
}

impl RootSubmissionHandle {
    fn new(submission: Submission) -> Self {
        Self { submission }
    }
}

#[derive(Clone)]
pub(crate) struct AttemptSubmissionHandle {
    pub(crate) port: Arc<dyn eos_types::AttemptSubmissionPort>,
}

impl AttemptSubmissionHandle {
    fn new(submission: &Submission) -> Self {
        Self {
            port: submission.attempt().expect("live tool runtime has attempt submission"),
        }
    }
}

#[derive(Clone)]
pub(crate) struct SkillHandle {
    pub(crate) skill_registry: Arc<SkillRegistry>,
}

impl SkillHandle {
    #[must_use]
    pub(crate) fn new(skill_registry: Arc<SkillRegistry>) -> Self {
        Self { skill_registry }
    }
}

/// Register one tool, stamping its intent / terminal flag / hooks from config.
pub(crate) fn register_tool(
    registry: &mut ToolRegistry,
    name: ToolName,
    config: &ToolConfig,
    spec: ToolSpec,
    output: OutputShape,
    executor: Arc<dyn ToolExecutor>,
) {
    registry.register(
        RegisteredTool::new(name, config.intent, config.terminal, spec, output, executor)
            .with_hooks(config.hooks.clone()),
    );
}

/// Build the default executable tool registry for one caller.
#[must_use]
pub fn build_default_registry(
    config: &ToolConfigSet,
    caller: &CallerScope,
    runtime: ToolRuntime,
) -> ToolRegistry {
    let mut registry = ToolRegistry::new();
    let sandbox = SandboxHandle::new(&runtime);
    let command = CommandHandle::new(&runtime);
    let background = BackgroundHandle::new(&runtime);
    let root = RootSubmissionHandle::new(runtime.submission.clone());
    let attempt = AttemptSubmissionHandle::new(&runtime.submission);
    let skills = SkillHandle::new(runtime.skills.clone());

    command::register(&mut registry, config, command);
    sandbox::register(&mut registry, config, sandbox);
    submission::register(&mut registry, config, root, attempt);
    ask_advisor::register(&mut registry, config, runtime.launcher.clone());
    workflow::register(&mut registry, config, runtime.workflow.clone(), background.clone());
    subagent::register(
        &mut registry,
        config,
        caller,
        runtime.launcher.clone(),
        background,
    );
    skills::register(&mut registry, config, caller, skills);
    registry.apply_hook_services(HookServices::new(&runtime));
    registry
}

/// Build the default schema-only registry for validation and snapshots.
#[must_use]
pub fn build_registry_schema(config: &ToolConfigSet, caller: &CallerScope) -> ToolRegistry {
    build_default_registry(config, caller, schema_runtime())
}

/// Parse-and-validate raw tool input into a typed DTO.
pub(crate) fn parse_input<T: DeserializeOwned>(
    tool: ToolName,
    raw: &JsonObject,
) -> Result<T, ToolResult> {
    serde_json::from_value::<T>(Value::Object(raw.clone())).map_err(|err| {
        ToolResult::error(format!(
            "Invalid input for {}: {err}. Please retry the tool call with valid arguments.",
            tool.as_str()
        ))
    })
}

#[cfg(test)]
pub(crate) fn repo_tools_config() -> ToolConfigSet {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../.eos-agents/tools");
    ToolConfigSet::load_from_dir(&root).expect("repo .eos-agents/tools loads and validates")
}

fn schema_runtime() -> ToolRuntime {
    let sandbox = Arc::new(SchemaOnlySandbox);
    let background = Arc::new(SchemaOnlyBackground);
    ToolRuntime {
        sandbox,
        workflow: Arc::new(SchemaOnlyWorkflow),
        launcher: Arc::new(SchemaOnlyLauncher),
        skills: Arc::new(SkillRegistry::new()),
        submission: Submission::schema(),
        background,
        workspace_mode: Arc::new(SchemaOnlyWorkspaceMode),
    }
}

#[derive(Debug)]
struct SchemaOnlySandbox;

#[async_trait]
impl SandboxTransport for SchemaOnlySandbox {
    async fn call(
        &self,
        _sandbox_id: &SandboxId,
        _op: DaemonOp,
        _payload: JsonObject,
        _timeout_s: u32,
    ) -> Result<JsonObject, SandboxPortError> {
        Ok(JsonObject::new())
    }
}

#[derive(Debug)]
struct SchemaOnlyBackground;

#[async_trait]
impl BackgroundSessions for SchemaOnlyBackground {
    async fn register_subagent(&self, _run: AgentRunId) -> Result<(), ToolError> {
        Err(ToolError::Internal("schema-only registry cannot execute tools".to_owned()))
    }

    async fn register_command(
        &self,
        _id: CommandSessionId,
        _sandbox: SandboxId,
    ) -> Result<(), ToolError> {
        Err(ToolError::Internal("schema-only registry cannot execute tools".to_owned()))
    }

    async fn register_workflow(&self, _started: StartedWorkflow) -> Result<(), ToolError> {
        Err(ToolError::Internal("schema-only registry cannot execute tools".to_owned()))
    }

    async fn cancel_subagent(&self, _run: AgentRunId, _reason: &str) -> Result<bool, ToolError> {
        Err(ToolError::Internal("schema-only registry cannot execute tools".to_owned()))
    }
}

#[derive(Debug)]
struct SchemaOnlyWorkspaceMode;

#[async_trait]
impl WorkspaceMode for SchemaOnlyWorkspaceMode {
    async fn set_isolated_workspace_mode(
        &self,
        _agent_run_id: AgentRunId,
        _is_isolated: bool,
    ) -> Result<(), ToolError> {
        Err(ToolError::Internal("schema-only registry cannot execute tools".to_owned()))
    }
}

#[derive(Debug)]
struct SchemaOnlyLauncher;

#[async_trait]
impl AgentRunApi for SchemaOnlyLauncher {
    async fn spawn_agent(&self, _request: SpawnAgentRequest) -> Result<AgentRunId, AgentRunError> {
        Err(AgentRunError::Internal(
            "schema-only registry cannot execute tools".to_owned(),
        ))
    }

    async fn wait_for_agent_outcome(
        &self,
        _agent_run_id: &AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError> {
        Err(AgentRunError::Internal(
            "schema-only registry cannot execute tools".to_owned(),
        ))
    }

    async fn poll_agent_run_outcome(
        &self,
        _agent_run_id: &AgentRunId,
    ) -> Result<Option<AgentRunOutcome>, AgentRunError> {
        Err(AgentRunError::Internal(
            "schema-only registry cannot execute tools".to_owned(),
        ))
    }

    async fn cancel_agent_run(
        &self,
        _agent_run_id: &AgentRunId,
        _reason: &str,
    ) -> Result<(), AgentRunError> {
        Err(AgentRunError::Internal(
            "schema-only registry cannot execute tools".to_owned(),
        ))
    }
}

#[derive(Debug)]
struct SchemaOnlyWorkflow;

#[async_trait]
impl WorkflowApi for SchemaOnlyWorkflow {
    async fn start_workflow(
        &self,
        _request: eos_types::StartWorkflowRequest,
    ) -> Result<StartedWorkflow, WorkflowApiError> {
        Err(WorkflowApiError::Internal(
            "schema-only registry cannot execute tools".to_owned(),
        ))
    }

    async fn check_workflow_status(
        &self,
        _workflow_id: &WorkflowId,
    ) -> Result<String, WorkflowApiError> {
        Err(WorkflowApiError::Internal(
            "schema-only registry cannot execute tools".to_owned(),
        ))
    }

    async fn cancel_workflow(
        &self,
        _workflow_id: &WorkflowId,
        _reason: &str,
    ) -> Result<String, WorkflowApiError> {
        Err(WorkflowApiError::Internal(
            "schema-only registry cannot execute tools".to_owned(),
        ))
    }

    async fn poll_terminal_workflow(
        &self,
        _workflow_id: &WorkflowId,
    ) -> Result<Option<eos_types::TerminalWorkflow>, WorkflowApiError> {
        Err(WorkflowApiError::Internal(
            "schema-only registry cannot execute tools".to_owned(),
        ))
    }

    async fn find_outstanding_workflows(
        &self,
        _task_id: &eos_types::TaskId,
        _agent_run_id: &AgentRunId,
    ) -> Result<Vec<eos_types::OutstandingWorkflow>, WorkflowApiError> {
        Err(WorkflowApiError::Internal(
            "schema-only registry cannot execute tools".to_owned(),
        ))
    }

    async fn workflow_depth(&self, _workflow_id: &WorkflowId) -> Result<u32, WorkflowApiError> {
        Err(WorkflowApiError::Internal(
            "schema-only registry cannot execute tools".to_owned(),
        ))
    }
}
