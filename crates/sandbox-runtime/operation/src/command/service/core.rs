use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use sandbox_runtime_command::CommandExecution;
use sandbox_runtime_namespace_execution::{
    ExecutionObserver, NamespaceExecutionEngine, NamespaceExecutionId,
};

use crate::command::CommandSessionId;
use crate::namespace_execution::{NamespaceExecutionLedger, NamespaceExecutionRecord};
use crate::observability::AsyncTraceSink;
use crate::workspace_crate::{
    CreateWorkspaceRequest, DestroyWorkspaceRequest, DestroyWorkspaceResult, WorkspaceProfile,
    WorkspaceSessionId,
};
use crate::workspace_remount::{ProcProcessGroupController, ProcessGroupController};
use crate::workspace_session::{
    WorkspaceSessionError, WorkspaceSessionHandler, WorkspaceSessionService,
};

const MAX_ACTIVE_COMMANDS: usize = 256;

const COMMAND_ENGINE_SETUP_TIMEOUT_S: f64 = 30.0;

pub struct CommandOperationService {
    workspace: Arc<WorkspaceSessionService>,
    config: ::sandbox_runtime_command::CommandConfig,
    engine: Arc<NamespaceExecutionEngine<CommandExecution>>,
    namespace_execution: Arc<NamespaceExecutionLedger>,
    async_trace_sink: Option<AsyncTraceSink>,
    remount_controller: Arc<dyn ProcessGroupController>,
    workspace_lifecycle_admission: Mutex<()>,
}

pub(crate) struct WorkspaceLifecycleAdmission<'a> {
    _guard: MutexGuard<'a, ()>,
}

impl CommandOperationService {
    #[must_use]
    pub fn new(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
    ) -> Self {
        Self::new_with_async_trace_sink(
            workspace,
            config,
            Arc::new(NamespaceExecutionLedger::new()),
            None,
        )
    }

    #[must_use]
    pub(crate) fn new_with_async_trace_sink(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
        namespace_execution: Arc<NamespaceExecutionLedger>,
        async_trace_sink: Option<AsyncTraceSink>,
    ) -> Self {
        let engine = build_engine(Arc::clone(&namespace_execution));
        Self::from_parts(
            workspace,
            config,
            engine,
            namespace_execution,
            async_trace_sink,
            Arc::new(ProcProcessGroupController),
        )
    }

    pub(super) fn from_parts(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
        engine: Arc<NamespaceExecutionEngine<CommandExecution>>,
        namespace_execution: Arc<NamespaceExecutionLedger>,
        async_trace_sink: Option<AsyncTraceSink>,
        remount_controller: Arc<dyn ProcessGroupController>,
    ) -> Self {
        Self {
            workspace,
            config,
            engine,
            namespace_execution,
            async_trace_sink,
            remount_controller,
            workspace_lifecycle_admission: Mutex::new(()),
        }
    }

    #[must_use]
    pub(crate) fn shares_workspace_session(
        &self,
        workspace: &Arc<WorkspaceSessionService>,
    ) -> bool {
        Arc::ptr_eq(&self.workspace, workspace)
    }

    #[must_use]
    pub(crate) fn shares_namespace_execution_store(
        &self,
        namespace_execution: &Arc<NamespaceExecutionLedger>,
    ) -> bool {
        Arc::ptr_eq(&self.namespace_execution, namespace_execution)
    }

    #[must_use]
    pub(crate) fn namespace_execution_store(&self) -> &Arc<NamespaceExecutionLedger> {
        &self.namespace_execution
    }

    #[doc(hidden)]
    pub fn drain_completed_namespace_executions_for_test(
        &self,
        limit: usize,
    ) -> Result<Vec<NamespaceExecutionRecord>, String> {
        self.namespace_execution
            .drain_completed_namespace_executions(limit)
    }

    #[must_use]
    pub fn config(&self) -> &::sandbox_runtime_command::CommandConfig {
        &self.config
    }

    #[must_use]
    pub(crate) fn engine(&self) -> &Arc<NamespaceExecutionEngine<CommandExecution>> {
        &self.engine
    }

    #[must_use]
    pub(crate) fn async_trace_sink(&self) -> Option<AsyncTraceSink> {
        self.async_trace_sink.clone()
    }

    #[must_use]
    pub(crate) fn live_command_session_ids_for_workspace(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> Vec<CommandSessionId> {
        let mut ids = self.engine.live_values(|command| {
            (command.workspace_session_id() == workspace_session_id)
                .then(|| command_session_id(command.id()))
        });
        ids.sort();
        ids
    }

    #[doc(hidden)]
    pub fn namespace_execution_id_for_command_for_test(
        &self,
        command_session_id: &CommandSessionId,
    ) -> Option<NamespaceExecutionId> {
        let id = execution_id(command_session_id);
        (self.engine.is_live(&id) || self.engine.is_completed(&id)).then_some(id)
    }

    #[must_use]
    pub(crate) fn remount_controller(&self) -> Arc<dyn ProcessGroupController> {
        Arc::clone(&self.remount_controller)
    }

    pub(crate) fn begin_workspace_lifecycle_admission(&self) -> WorkspaceLifecycleAdmission<'_> {
        let guard = self
            .workspace_lifecycle_admission
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        WorkspaceLifecycleAdmission { _guard: guard }
    }

    pub(crate) fn with_workspace_destroy_admission<R>(
        &self,
        workspace_session_id: &WorkspaceSessionId,
        dispatch: impl FnOnce(&[CommandSessionId]) -> R,
    ) -> R {
        let _lifecycle_admission = self.begin_workspace_lifecycle_admission();
        let active_command_session_ids =
            self.live_command_session_ids_for_workspace(workspace_session_id);
        dispatch(&active_command_session_ids)
    }

    pub(crate) fn resolve_workspace_session(
        &self,
        workspace_session_id: WorkspaceSessionId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        self.workspace.resolve_session(workspace_session_id)
    }

    pub(super) fn create_one_shot_workspace_session(
        &self,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        self.workspace
            .create_workspace_session(CreateWorkspaceRequest {
                profile: WorkspaceProfile::HostCompatible,
            })
    }

    pub(super) fn destroy_one_shot_workspace_session(
        &self,
        handler: WorkspaceSessionHandler,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        self.workspace
            .destroy_session(handler, DestroyWorkspaceRequest::default())
    }

    pub(super) fn workspace_handle(&self) -> &Arc<WorkspaceSessionService> {
        &self.workspace
    }

    pub(super) fn workspace_remount_pending(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> bool {
        self.workspace.is_remount_pending(workspace_session_id)
    }

    pub(super) fn workspace_remount_blocked(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> bool {
        self.workspace.is_remount_blocked(workspace_session_id)
    }
}

fn build_engine(
    namespace_execution: Arc<NamespaceExecutionLedger>,
) -> Arc<NamespaceExecutionEngine<CommandExecution>> {
    Arc::new(NamespaceExecutionEngine::new(
        namespace_execution as Arc<dyn ExecutionObserver>,
        MAX_ACTIVE_COMMANDS,
        COMMAND_ENGINE_SETUP_TIMEOUT_S,
    ))
}

pub(crate) fn execution_id(command_session_id: &CommandSessionId) -> NamespaceExecutionId {
    NamespaceExecutionId(command_session_id.0.clone())
}

pub(crate) fn command_session_id(id: &NamespaceExecutionId) -> CommandSessionId {
    CommandSessionId(id.0.clone())
}
