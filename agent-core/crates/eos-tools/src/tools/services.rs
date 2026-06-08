//! Local service structs captured by tool executors and hook wiring.
//!
//! These are intentionally small, family-specific dependency sets. Runtime
//! provider boundaries remain `dyn Trait`; closed groupings stay concrete.

use std::future::Future;
use std::pin::Pin;
use std::{fmt, sync::Arc};

use async_trait::async_trait;
use eos_sandbox_port::{
    DaemonOp, SandboxCommandApi, SandboxCommandService, SandboxPortError, SandboxTransport,
};
use eos_skills::SkillRegistry;
use eos_types::{AgentRunId, CommandSessionId, JsonObject, SandboxId};
use eos_types::{RequestStore, StartedWorkflow, TaskStore, WorkflowApi};

use crate::{AttemptSubmissionPort, ToolError};

type BoxServiceFuture<T> = Pin<Box<dyn Future<Output = T> + Send + 'static>>;
type RegisterSubagentSession = Arc<dyn Fn(AgentRunId) -> BoxServiceFuture<()> + Send + Sync>;
type CancelSubagentSession =
    Arc<dyn Fn(AgentRunId, String) -> BoxServiceFuture<bool> + Send + Sync>;
type CountSubagentSessions = Arc<dyn Fn() -> BoxServiceFuture<usize> + Send + Sync>;
type CancelAllSubagentSessions = Arc<dyn Fn(String) -> BoxServiceFuture<()> + Send + Sync>;
type RegisterWorkflowSession = Arc<dyn Fn(StartedWorkflow) -> BoxServiceFuture<()> + Send + Sync>;
type RegisterCommandSession =
    Arc<dyn Fn(CommandSessionId, SandboxId) -> BoxServiceFuture<()> + Send + Sync>;

/// Store access for the root terminal.
#[derive(Clone)]
pub struct RootSubmissionService {
    pub(crate) task_store: Arc<dyn TaskStore>,
    pub(crate) request_store: Arc<dyn RequestStore>,
}

impl RootSubmissionService {
    /// Build the root-submission service over persisted request/task stores.
    #[must_use]
    pub fn new(task_store: Arc<dyn TaskStore>, request_store: Arc<dyn RequestStore>) -> Self {
        Self {
            task_store,
            request_store,
        }
    }
}

impl fmt::Debug for RootSubmissionService {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RootSubmissionService")
            .finish_non_exhaustive()
    }
}

/// Attempt terminal submission behavior.
#[derive(Clone)]
pub struct AttemptSubmissionService {
    pub(crate) port: Arc<dyn AttemptSubmissionPort>,
}

impl AttemptSubmissionService {
    /// Build the attempt-submission service.
    #[must_use]
    pub fn new(port: Arc<dyn AttemptSubmissionPort>) -> Self {
        Self { port }
    }
}

impl fmt::Debug for AttemptSubmissionService {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AttemptSubmissionService")
            .finish_non_exhaustive()
    }
}

/// Sandbox RPC access for file, shell, plugin, and isolated-workspace tools.
#[derive(Clone)]
pub struct SandboxToolService {
    pub(crate) transport: Arc<dyn SandboxTransport>,
}

impl SandboxToolService {
    /// Build the sandbox tool service over the daemon transport.
    #[must_use]
    pub fn new(transport: Arc<dyn SandboxTransport>) -> Self {
        Self { transport }
    }

    /// Clone the underlying sandbox transport for related service wiring.
    #[must_use]
    pub fn transport(&self) -> Arc<dyn SandboxTransport> {
        self.transport.clone()
    }
}

impl fmt::Debug for SandboxToolService {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SandboxToolService").finish_non_exhaustive()
    }
}

/// Command-session tool dependencies.
#[derive(Clone)]
pub struct CommandToolService {
    pub(crate) command_service: Arc<dyn SandboxCommandApi>,
    pub(crate) command_sessions: Option<CommandSessionToolService>,
}

impl CommandToolService {
    /// Build command tool services from the daemon sandbox transport.
    #[must_use]
    pub fn new(
        transport: Arc<dyn SandboxTransport>,
        command_sessions: Option<CommandSessionToolService>,
    ) -> Self {
        Self::with_command_service(
            Arc::new(SandboxCommandService::new(transport)),
            command_sessions,
        )
    }

    /// Build command tool services from the command resource service.
    #[must_use]
    pub fn with_command_service(
        command_service: Arc<dyn SandboxCommandApi>,
        command_sessions: Option<CommandSessionToolService>,
    ) -> Self {
        Self {
            command_service,
            command_sessions,
        }
    }
}

impl fmt::Debug for CommandToolService {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CommandToolService")
            .field("has_command_sessions", &self.command_sessions.is_some())
            .finish_non_exhaustive()
    }
}

/// Command-session background tracking captured by shell tools. Runtime wiring
/// supplies the engine-owned registration callback for the current agent run.
#[derive(Clone, Default)]
pub struct CommandSessionToolService {
    register: Option<RegisterCommandSession>,
}

impl CommandSessionToolService {
    /// Build command-session tracking from a run-local registration callback.
    #[must_use]
    pub fn new<Register, RegisterFuture>(register: Register) -> Self
    where
        Register: Fn(CommandSessionId, SandboxId) -> RegisterFuture + Send + Sync + 'static,
        RegisterFuture: Future<Output = ()> + Send + 'static,
    {
        Self {
            register: Some(Arc::new(move |command_session_id, sandbox_id| {
                Box::pin(register(command_session_id, sandbox_id))
            })),
        }
    }

    pub(crate) async fn register_background_session(
        &self,
        command_session_id: &CommandSessionId,
        sandbox_id: &SandboxId,
    ) -> Result<(), ToolError> {
        let Some(register) = &self.register else {
            return Err(ToolError::MissingPort("command_sessions"));
        };
        register(command_session_id.clone(), sandbox_id.clone()).await;
        Ok(())
    }
}

impl fmt::Debug for CommandSessionToolService {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CommandSessionToolService")
            .field("has_register", &self.register.is_some())
            .finish()
    }
}

/// Subagent background-session dependencies captured by subagent tools and
/// hooks. This is a concrete tool service, not a public port trait; runtime
/// wiring supplies the engine-owned callbacks for the current parent run.
#[derive(Clone, Default)]
pub struct SubagentToolService {
    register: Option<RegisterSubagentSession>,
    cancel_one: Option<CancelSubagentSession>,
    count: Option<CountSubagentSessions>,
    cancel_all: Option<CancelAllSubagentSessions>,
}

impl SubagentToolService {
    /// Build a subagent tool service from run-local callbacks.
    #[must_use]
    pub fn new<
        Register,
        RegisterFuture,
        Cancel,
        CancelFuture,
        Count,
        CountFuture,
        CancelAll,
        CancelAllFuture,
    >(
        register: Register,
        cancel_one: Cancel,
        count: Count,
        cancel_all: CancelAll,
    ) -> Self
    where
        Register: Fn(AgentRunId) -> RegisterFuture + Send + Sync + 'static,
        RegisterFuture: Future<Output = ()> + Send + 'static,
        Cancel: Fn(AgentRunId, String) -> CancelFuture + Send + Sync + 'static,
        CancelFuture: Future<Output = bool> + Send + 'static,
        Count: Fn() -> CountFuture + Send + Sync + 'static,
        CountFuture: Future<Output = usize> + Send + 'static,
        CancelAll: Fn(String) -> CancelAllFuture + Send + Sync + 'static,
        CancelAllFuture: Future<Output = ()> + Send + 'static,
    {
        Self {
            register: Some(Arc::new(move |agent_run_id| {
                Box::pin(register(agent_run_id))
            })),
            cancel_one: Some(Arc::new(move |agent_run_id, reason| {
                Box::pin(cancel_one(agent_run_id, reason))
            })),
            count: Some(Arc::new(move || Box::pin(count()))),
            cancel_all: Some(Arc::new(move |reason| Box::pin(cancel_all(reason)))),
        }
    }

    pub(crate) async fn register_background_session(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<(), ToolError> {
        let Some(register) = &self.register else {
            return Err(ToolError::MissingPort("subagent_sessions"));
        };
        register(agent_run_id.clone()).await;
        Ok(())
    }

    pub(crate) async fn cancel_background_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<bool, ToolError> {
        let Some(cancel_one) = &self.cancel_one else {
            return Err(ToolError::MissingPort("subagent_sessions"));
        };
        Ok(cancel_one(agent_run_id.clone(), reason.to_owned()).await)
    }

    pub(crate) async fn count_background_sessions(&self) -> Result<usize, ToolError> {
        let Some(count) = &self.count else {
            return Err(ToolError::MissingPort("subagent_sessions"));
        };
        Ok(count().await)
    }

    pub(crate) async fn cancel_all_background_sessions(
        &self,
        reason: &str,
    ) -> Result<(), ToolError> {
        let Some(cancel_all) = &self.cancel_all else {
            return Err(ToolError::MissingPort("subagent_sessions"));
        };
        cancel_all(reason.to_owned()).await;
        Ok(())
    }
}

impl fmt::Debug for SubagentToolService {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SubagentToolService")
            .field("has_register", &self.register.is_some())
            .field("has_cancel_one", &self.cancel_one.is_some())
            .field("has_count", &self.count.is_some())
            .field("has_cancel_all", &self.cancel_all.is_some())
            .finish()
    }
}

/// Workflow background-session dependencies captured by workflow tools. Runtime
/// wiring supplies the engine-owned callback for the current parent run.
#[derive(Clone, Default)]
pub struct WorkflowToolService {
    register: Option<RegisterWorkflowSession>,
}

impl WorkflowToolService {
    /// Build a workflow tool service from a run-local registration callback.
    #[must_use]
    pub fn new<Register, RegisterFuture>(register: Register) -> Self
    where
        Register: Fn(StartedWorkflow) -> RegisterFuture + Send + Sync + 'static,
        RegisterFuture: Future<Output = ()> + Send + 'static,
    {
        Self {
            register: Some(Arc::new(move |workflow| Box::pin(register(workflow)))),
        }
    }

    pub(crate) async fn register_background_session(
        &self,
        workflow: &StartedWorkflow,
    ) -> Result<(), ToolError> {
        let Some(register) = &self.register else {
            return Err(ToolError::MissingPort("workflow_sessions"));
        };
        register(workflow.clone()).await;
        Ok(())
    }
}

impl fmt::Debug for WorkflowToolService {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WorkflowToolService")
            .field("has_register", &self.register.is_some())
            .finish()
    }
}

/// Skill registry access for skill-reference tools.
#[derive(Clone)]
pub struct SkillToolService {
    pub(crate) skill_registry: Arc<SkillRegistry>,
}

impl SkillToolService {
    /// Build skill tool services.
    #[must_use]
    pub fn new(skill_registry: Arc<SkillRegistry>) -> Self {
        Self { skill_registry }
    }
}

impl fmt::Debug for SkillToolService {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SkillToolService").finish_non_exhaustive()
    }
}

/// State-dependent pre-hook dependencies.
#[derive(Clone, Default)]
pub struct HookServices {
    pub(crate) sandbox_transport: Option<Arc<dyn SandboxTransport>>,
    pub(crate) workflow_service: Option<Arc<dyn WorkflowApi>>,
    pub(crate) subagent_sessions: Option<SubagentToolService>,
}

impl HookServices {
    /// Build hook services for tools that need downstream state during pre-hooks.
    #[must_use]
    pub fn new(
        sandbox_transport: Option<Arc<dyn SandboxTransport>>,
        workflow_service: Option<Arc<dyn WorkflowApi>>,
        subagent_sessions: Option<SubagentToolService>,
    ) -> Self {
        Self {
            sandbox_transport,
            workflow_service,
            subagent_sessions,
        }
    }
}

impl fmt::Debug for HookServices {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("HookServices")
            .field("has_sandbox_transport", &self.sandbox_transport.is_some())
            .field("has_workflow_service", &self.workflow_service.is_some())
            .field("has_subagent_sessions", &self.subagent_sessions.is_some())
            .finish()
    }
}

/// Inert transport used only when building a static registry without executable
/// services, such as schema snapshots and registry validation.
#[derive(Debug)]
pub(crate) struct InertSandboxTransport;

#[async_trait]
impl SandboxTransport for InertSandboxTransport {
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
