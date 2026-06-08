//! Family-specific tool session service wrappers.

use std::future::Future;
use std::pin::Pin;
use std::{fmt, sync::Arc};

use eos_sandbox_port::SandboxTransport;
use eos_types::{AgentRunId, CommandSessionId, SandboxId, StartedWorkflow, WorkflowApi};

use crate::ToolError;

type BoxServiceFuture<T> = Pin<Box<dyn Future<Output = T> + Send + 'static>>;
type RegisterSubagentSession = Arc<dyn Fn(AgentRunId) -> BoxServiceFuture<()> + Send + Sync>;
type CancelSubagentSession =
    Arc<dyn Fn(AgentRunId, String) -> BoxServiceFuture<bool> + Send + Sync>;
type CountSubagentSessions = Arc<dyn Fn() -> BoxServiceFuture<usize> + Send + Sync>;
type CancelAllSubagentSessions = Arc<dyn Fn(String) -> BoxServiceFuture<()> + Send + Sync>;
type RegisterWorkflowSession = Arc<dyn Fn(StartedWorkflow) -> BoxServiceFuture<()> + Send + Sync>;
type RegisterCommandSession =
    Arc<dyn Fn(CommandSessionId, SandboxId) -> BoxServiceFuture<()> + Send + Sync>;

/// Command-session background tracking captured by shell tools.
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

    /// Register a background command session.
    ///
    /// # Errors
    /// Returns [`ToolError::MissingPort`] when the callback is absent.
    pub async fn register_background_session(
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

/// Subagent background-session dependencies captured by subagent tools and hooks.
#[derive(Clone, Default)]
pub struct SubagentToolService {
    register: Option<RegisterSubagentSession>,
    cancel_one: Option<CancelSubagentSession>,
    count: Option<CountSubagentSessions>,
    cancel_all: Option<CancelAllSubagentSessions>,
}

impl SubagentToolService {
    /// Build a subagent tool service from run-local callbacks.
    #[allow(clippy::too_many_arguments)]
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

    /// Register a background subagent run.
    ///
    /// # Errors
    /// Returns [`ToolError::MissingPort`] when the callback is absent.
    pub async fn register_background_session(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<(), ToolError> {
        let Some(register) = &self.register else {
            return Err(ToolError::MissingPort("subagent_sessions"));
        };
        register(agent_run_id.clone()).await;
        Ok(())
    }

    /// Cancel one background subagent run.
    ///
    /// # Errors
    /// Returns [`ToolError::MissingPort`] when the callback is absent.
    pub async fn cancel_background_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<bool, ToolError> {
        let Some(cancel_one) = &self.cancel_one else {
            return Err(ToolError::MissingPort("subagent_sessions"));
        };
        Ok(cancel_one(agent_run_id.clone(), reason.to_owned()).await)
    }

    /// Count background subagent runs.
    ///
    /// # Errors
    /// Returns [`ToolError::MissingPort`] when the callback is absent.
    pub async fn count_background_sessions(&self) -> Result<usize, ToolError> {
        let Some(count) = &self.count else {
            return Err(ToolError::MissingPort("subagent_sessions"));
        };
        Ok(count().await)
    }

    /// Cancel all background subagent runs.
    ///
    /// # Errors
    /// Returns [`ToolError::MissingPort`] when the callback is absent.
    pub async fn cancel_all_background_sessions(&self, reason: &str) -> Result<(), ToolError> {
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

/// Workflow background-session dependencies captured by workflow tools.
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

    /// Register a background workflow.
    ///
    /// # Errors
    /// Returns [`ToolError::MissingPort`] when the callback is absent.
    pub async fn register_background_session(
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

/// State-dependent pre-hook dependencies.
#[derive(Clone, Default)]
pub struct HookServices {
    sandbox_transport: Option<Arc<dyn SandboxTransport>>,
    workflow_service: Option<Arc<dyn WorkflowApi>>,
    subagent_sessions: Option<SubagentToolService>,
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

    /// Sandbox transport used by command-session hook checks.
    #[must_use]
    pub fn sandbox_transport(&self) -> Option<&Arc<dyn SandboxTransport>> {
        self.sandbox_transport.as_ref()
    }

    /// Workflow state API used by workflow hook checks.
    #[must_use]
    pub fn workflow_service(&self) -> Option<&Arc<dyn WorkflowApi>> {
        self.workflow_service.as_ref()
    }

    /// Subagent background-session service used by hook checks.
    #[must_use]
    pub fn subagent_sessions(&self) -> Option<&SubagentToolService> {
        self.subagent_sessions.as_ref()
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
