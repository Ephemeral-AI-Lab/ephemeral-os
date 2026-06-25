use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use sandbox_runtime_namespace_execution::{
    NamespaceExecutionEngine, NamespaceExecutionId, NoopObserver,
};

use crate::command::{CommandConfig, CommandExecValue};
use crate::namespace_execution::{NamespaceExecutionLedger, RuntimeNamespaceExecutionSnapshot};
use crate::observability::AsyncTraceSink;
use crate::workspace_crate::{
    CreateWorkspaceRequest, DestroyWorkspaceRequest, DestroyWorkspaceResult, WorkspaceProfile,
    WorkspaceSessionId,
};
use crate::workspace_session::{
    WorkspaceSessionError, WorkspaceSessionHandler, WorkspaceSessionService,
};

const MAX_ACTIVE_COMMANDS: usize = 256;

const COMMAND_ENGINE_SETUP_TIMEOUT_S: f64 = 30.0;

pub struct CommandOperationService {
    workspace: Arc<WorkspaceSessionService>,
    config: CommandConfig,
    engine: Arc<NamespaceExecutionEngine<CommandExecValue>>,
    namespace_execution: Arc<NamespaceExecutionLedger>,
    async_trace_sink: Option<AsyncTraceSink>,
    workspace_lifecycle_admission: Mutex<()>,
}

pub(crate) type WorkspaceLifecycleAdmission<'a> = MutexGuard<'a, ()>;

impl CommandOperationService {
    #[must_use]
    pub fn new(
        workspace: Arc<WorkspaceSessionService>,
        config: CommandConfig,
        async_trace_sink: Option<AsyncTraceSink>,
    ) -> Self {
        let engine = Arc::new(NamespaceExecutionEngine::new(
            Arc::new(NoopObserver),
            MAX_ACTIVE_COMMANDS,
            COMMAND_ENGINE_SETUP_TIMEOUT_S,
        ));
        Self::with_engine(
            workspace,
            config,
            engine,
            Arc::new(NamespaceExecutionLedger::new()),
            async_trace_sink,
        )
    }

    /// Build a command service over a caller-supplied engine. The test harness
    /// wires that engine to a local fake launcher; production goes through `new`.
    #[doc(hidden)]
    #[must_use]
    pub fn with_engine(
        workspace: Arc<WorkspaceSessionService>,
        config: CommandConfig,
        engine: Arc<NamespaceExecutionEngine<CommandExecValue>>,
        namespace_execution: Arc<NamespaceExecutionLedger>,
        async_trace_sink: Option<AsyncTraceSink>,
    ) -> Self {
        Self {
            workspace,
            config,
            engine,
            namespace_execution,
            async_trace_sink,
            workspace_lifecycle_admission: Mutex::new(()),
        }
    }

    #[must_use]
    pub fn namespace_execution_store(&self) -> &Arc<NamespaceExecutionLedger> {
        &self.namespace_execution
    }

    #[must_use]
    pub fn active_namespace_executions(&self) -> Vec<RuntimeNamespaceExecutionSnapshot> {
        let mut snapshots = self.engine.live_values(|command| {
            Some(RuntimeNamespaceExecutionSnapshot {
                namespace_execution_id: command.exec.id().clone(),
                workspace_session_id: command.workspace_session_id.clone(),
                operation_name: command.operation_name.to_owned(),
            })
        });
        snapshots.sort_by(|left, right| {
            left.namespace_execution_id
                .cmp(&right.namespace_execution_id)
        });
        snapshots
    }

    #[must_use]
    pub fn config(&self) -> &CommandConfig {
        &self.config
    }

    #[must_use]
    pub(crate) fn engine(&self) -> &Arc<NamespaceExecutionEngine<CommandExecValue>> {
        &self.engine
    }

    #[must_use]
    pub(crate) fn async_trace_sink(&self) -> Option<AsyncTraceSink> {
        self.async_trace_sink.clone()
    }

    pub(crate) fn begin_workspace_lifecycle_admission(&self) -> WorkspaceLifecycleAdmission<'_> {
        self.workspace_lifecycle_admission
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }

    pub(crate) fn with_workspace_destroy_admission<R>(
        &self,
        workspace_session_id: &WorkspaceSessionId,
        dispatch: impl FnOnce(&[NamespaceExecutionId]) -> R,
    ) -> R {
        let _lifecycle_admission = self.begin_workspace_lifecycle_admission();
        let mut active_command_session_ids = self.engine.live_values(|command| {
            (command.workspace_session_id == *workspace_session_id)
                .then(|| command.exec.id().clone())
        });
        active_command_session_ids.sort();
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
}
