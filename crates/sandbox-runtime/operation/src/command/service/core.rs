use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use crate::command::{CommandLaunchDriver, CommandProcessStore, RealCommandLaunchDriver};
use crate::workspace_crate::{noop_runtime_metrics_recorder, RuntimeMetricsRecorderHandle};
use crate::workspace_remount::{ProcProcessGroupController, ProcessGroupController};
use crate::workspace_session::WorkspaceSessionService;
use sandbox_runtime_namespace_process::runner::protocol::{no_trace_context, CurrentTraceContext};

pub struct CommandOperationService {
    workspace: Arc<WorkspaceSessionService>,
    config: ::sandbox_runtime_command::CommandConfig,
    process_store: Arc<CommandProcessStore>,
    launch_driver: Arc<dyn CommandLaunchDriver>,
    remount_controller: Arc<dyn ProcessGroupController>,
    remount_admission: Mutex<()>,
    metrics: RuntimeMetricsRecorderHandle,
    current_trace_context: CurrentTraceContext,
}

impl CommandOperationService {
    #[must_use]
    pub fn new(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
    ) -> Self {
        Self::new_with_metrics(workspace, config, noop_runtime_metrics_recorder())
    }

    #[must_use]
    pub fn new_with_metrics(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
        metrics: RuntimeMetricsRecorderHandle,
    ) -> Self {
        Self::new_with_metrics_and_current_trace_context(
            workspace,
            config,
            metrics,
            no_trace_context(),
        )
    }

    #[must_use]
    pub(crate) fn new_with_metrics_and_current_trace_context(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
        metrics: RuntimeMetricsRecorderHandle,
        current_trace_context: CurrentTraceContext,
    ) -> Self {
        Self::from_parts(
            workspace,
            config,
            Arc::new(RealCommandLaunchDriver),
            Arc::new(ProcProcessGroupController),
            metrics,
            current_trace_context,
        )
    }

    #[doc(hidden)]
    #[must_use]
    pub fn with_launch_driver_for_test(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
        launch_driver: Arc<dyn CommandLaunchDriver>,
    ) -> Self {
        Self::from_parts(
            workspace,
            config,
            launch_driver,
            Arc::new(ProcProcessGroupController),
            noop_runtime_metrics_recorder(),
            no_trace_context(),
        )
    }

    #[doc(hidden)]
    #[must_use]
    pub fn with_launch_driver_and_remount_controller_for_test(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
        launch_driver: Arc<dyn CommandLaunchDriver>,
        remount_controller: Arc<dyn ProcessGroupController>,
    ) -> Self {
        Self::from_parts(
            workspace,
            config,
            launch_driver,
            remount_controller,
            noop_runtime_metrics_recorder(),
            no_trace_context(),
        )
    }

    #[doc(hidden)]
    #[must_use]
    pub fn with_launch_driver_and_current_trace_context_for_test(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
        launch_driver: Arc<dyn CommandLaunchDriver>,
        current_trace_context: CurrentTraceContext,
    ) -> Self {
        Self::from_parts(
            workspace,
            config,
            launch_driver,
            Arc::new(ProcProcessGroupController),
            noop_runtime_metrics_recorder(),
            current_trace_context,
        )
    }

    fn from_parts(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
        launch_driver: Arc<dyn CommandLaunchDriver>,
        remount_controller: Arc<dyn ProcessGroupController>,
        metrics: RuntimeMetricsRecorderHandle,
        current_trace_context: CurrentTraceContext,
    ) -> Self {
        Self {
            workspace,
            config,
            process_store: Arc::new(CommandProcessStore::new()),
            launch_driver,
            remount_controller,
            remount_admission: Mutex::new(()),
            metrics,
            current_trace_context,
        }
    }

    #[must_use]
    pub(crate) fn workspace(&self) -> &Arc<WorkspaceSessionService> {
        &self.workspace
    }

    #[must_use]
    pub fn config(&self) -> &::sandbox_runtime_command::CommandConfig {
        &self.config
    }

    #[must_use]
    pub(crate) fn process_store(&self) -> &Arc<CommandProcessStore> {
        &self.process_store
    }

    #[must_use]
    pub(crate) fn launch_driver(&self) -> &Arc<dyn CommandLaunchDriver> {
        &self.launch_driver
    }

    #[must_use]
    pub(crate) fn remount_controller(&self) -> Arc<dyn ProcessGroupController> {
        Arc::clone(&self.remount_controller)
    }

    #[must_use]
    pub(crate) fn metrics(&self) -> &RuntimeMetricsRecorderHandle {
        &self.metrics
    }

    #[must_use]
    pub(crate) fn current_trace_context(
        &self,
    ) -> Option<sandbox_runtime_namespace_process::runner::protocol::TraceContext> {
        (self.current_trace_context)()
    }

    pub(crate) fn lock_remount_admission(&self) -> MutexGuard<'_, ()> {
        self.remount_admission
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }
}
