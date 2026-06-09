//! Concrete backend-facing agent-core service.

use std::sync::Arc;

use eos_agent_run::AgentRunService;
use eos_sandbox_port::SandboxGateway;
use eos_types::{
    AgentName, AgentRunStore, AttemptStore, IterationStore, Page, PageResult, RequestId,
    RequestStore, TaskAgentRunStore, TaskRun, TaskStore, WorkflowStore,
};

use crate::dto::{
    CancelUserRequestInput, CancelUserRequestOutput, CreateUserRequestInput,
    CreateUserRequestOutput, UserRequestDetail, UserRequestSummary,
};
use crate::error::AgentCoreServerError;
use crate::request_state::RequestState;

/// Backend-facing request service.
#[derive(Clone)]
pub struct AgentCoreService {
    pub(crate) request_store: Arc<dyn RequestStore>,
    // Required by the Phase 05 constructor/service contract; backend task and
    // agent-run read routes still receive these store handles separately.
    #[allow(dead_code)]
    pub(crate) task_store: Arc<dyn TaskStore>,
    // Required by the Phase 05 constructor/service contract; agent-run records
    // remain outside this service's request lifecycle methods.
    #[allow(dead_code)]
    pub(crate) agent_run_store: Arc<dyn AgentRunStore>,
    pub(crate) task_agent_run_store: Arc<dyn TaskAgentRunStore>,
    pub(crate) workflow_store: Arc<dyn WorkflowStore>,
    pub(crate) iteration_store: Arc<dyn IterationStore>,
    pub(crate) attempt_store: Arc<dyn AttemptStore>,
    pub(crate) agent_run_service: AgentRunService,
    pub(crate) sandbox_gateway: Arc<dyn SandboxGateway>,
    pub(crate) settings: AgentCoreServiceSettings,
}

impl std::fmt::Debug for AgentCoreService {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("AgentCoreService")
            .field("settings", &self.settings)
            .finish_non_exhaustive()
    }
}

/// Fixed settings for [`AgentCoreService`].
#[derive(Debug, Clone)]
pub struct AgentCoreServiceSettings {
    /// Request-visible workspace root.
    pub workspace_root: String,
    /// Root agent profile name.
    pub root_agent_name: AgentName,
}

/// Constructor dependencies for [`AgentCoreService`].
pub struct AgentCoreServiceDependencies {
    /// Durable top-level request rows.
    pub request_store: Arc<dyn RequestStore>,
    /// Durable task rows.
    pub task_store: Arc<dyn TaskStore>,
    /// Durable compatibility agent-run rows.
    pub agent_run_store: Arc<dyn AgentRunStore>,
    /// Durable task-agent-run lineage rows.
    pub task_agent_run_store: Arc<dyn TaskAgentRunStore>,
    /// Workflow rows.
    pub workflow_store: Arc<dyn WorkflowStore>,
    /// Iteration rows.
    pub iteration_store: Arc<dyn IterationStore>,
    /// Attempt rows.
    pub attempt_store: Arc<dyn AttemptStore>,
    /// Active and durable agent-run lifecycle.
    pub agent_run_service: AgentRunService,
    /// Sandbox binding plus sandbox tool transport.
    pub sandbox_gateway: Arc<dyn SandboxGateway>,
    /// Fixed service settings.
    pub settings: AgentCoreServiceSettings,
}

impl std::fmt::Debug for AgentCoreServiceDependencies {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("AgentCoreServiceDependencies")
            .field("settings", &self.settings)
            .finish_non_exhaustive()
    }
}

impl AgentCoreService {
    /// Build a service from explicit dependencies.
    #[must_use]
    pub fn new(dependencies: AgentCoreServiceDependencies) -> Self {
        Self {
            request_store: dependencies.request_store,
            task_store: dependencies.task_store,
            agent_run_store: dependencies.agent_run_store,
            task_agent_run_store: dependencies.task_agent_run_store,
            workflow_store: dependencies.workflow_store,
            iteration_store: dependencies.iteration_store,
            attempt_store: dependencies.attempt_store,
            agent_run_service: dependencies.agent_run_service,
            sandbox_gateway: dependencies.sandbox_gateway,
            settings: dependencies.settings,
        }
    }

    pub(crate) fn request_state(&self) -> RequestState {
        RequestState {
            request_store: self.request_store.clone(),
            task_agent_run_store: self.task_agent_run_store.clone(),
            workflow_store: self.workflow_store.clone(),
            iteration_store: self.iteration_store.clone(),
            attempt_store: self.attempt_store.clone(),
        }
    }

    /// Create a user request and start its root agent run.
    ///
    /// # Errors
    /// Returns [`AgentCoreServerError`] when provisioning, persistence, or
    /// spawning fails.
    pub async fn create_user_request(
        &self,
        input: CreateUserRequestInput,
    ) -> Result<CreateUserRequestOutput, AgentCoreServerError> {
        crate::user_request::create::create_user_request(self, input).await
    }

    /// Cancel one user request.
    ///
    /// # Errors
    /// Returns [`AgentCoreServerError`] when the request is absent, already
    /// terminal, or cancellation persistence fails.
    pub async fn cancel_user_request(
        &self,
        input: CancelUserRequestInput,
    ) -> Result<CancelUserRequestOutput, AgentCoreServerError> {
        crate::user_request::cancel::cancel_user_request(
            &self.request_state(),
            &self.agent_run_service,
            input,
        )
        .await
    }

    /// Read one user request.
    ///
    /// # Errors
    /// Returns [`AgentCoreServerError`] on store failure.
    pub async fn read_user_request(
        &self,
        request_id: &RequestId,
    ) -> Result<Option<UserRequestDetail>, AgentCoreServerError> {
        crate::user_request::read::read_user_request(&self.request_state(), request_id).await
    }

    /// List user request summaries.
    ///
    /// # Errors
    /// Returns [`AgentCoreServerError`] on store failure.
    pub async fn list_user_requests(
        &self,
        page: Page,
    ) -> Result<PageResult<UserRequestSummary>, AgentCoreServerError> {
        crate::user_request::list::list_user_requests(&self.request_state(), page).await
    }

    /// List task-agent-runs for a user request.
    ///
    /// # Errors
    /// Returns [`AgentCoreServerError`] on store failure.
    pub async fn list_user_request_tasks(
        &self,
        request_id: &RequestId,
    ) -> Result<Vec<TaskRun>, AgentCoreServerError> {
        crate::user_request::list_tasks::list_user_request_tasks(&self.request_state(), request_id)
            .await
    }
}
