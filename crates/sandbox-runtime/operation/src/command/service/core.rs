use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use crate::command::{
    CommandLaunchDriver, CommandProcessStore, CommandSessionId, RealCommandLaunchDriver,
};
use crate::workspace_crate::WorkspaceSessionId;
use crate::workspace_remount::{ProcProcessGroupController, ProcessGroupController};
use crate::workspace_session::WorkspaceSessionService;

use super::completion::{spawn_completion_finalizer, CommandCompletionSender};

pub struct CommandOperationService {
    workspace: Arc<WorkspaceSessionService>,
    config: ::sandbox_runtime_command::CommandConfig,
    process_store: Arc<CommandProcessStore>,
    launch_driver: Arc<dyn CommandLaunchDriver>,
    completion_sender: CommandCompletionSender,
    remount_controller: Arc<dyn ProcessGroupController>,
    workspace_lifecycle_admission: Mutex<()>,
}

pub(crate) struct WorkspaceLifecycleAdmission<'a> {
    _guard: MutexGuard<'a, ()>,
}

pub(crate) struct WorkspaceDestroyAdmission<'a> {
    pub(crate) active_command_session_ids: Vec<CommandSessionId>,
    _lifecycle_admission: WorkspaceLifecycleAdmission<'a>,
}

impl CommandOperationService {
    #[must_use]
    pub fn new(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
    ) -> Self {
        Self::from_parts(
            workspace,
            config,
            Arc::new(RealCommandLaunchDriver),
            Arc::new(ProcProcessGroupController),
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
        Self::from_parts(workspace, config, launch_driver, remount_controller)
    }

    fn from_parts(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
        launch_driver: Arc<dyn CommandLaunchDriver>,
        remount_controller: Arc<dyn ProcessGroupController>,
    ) -> Self {
        let process_store = Arc::new(CommandProcessStore::new());
        let completion_sender =
            spawn_completion_finalizer(Arc::clone(&workspace), Arc::clone(&process_store));
        Self {
            workspace,
            config,
            process_store,
            launch_driver,
            completion_sender,
            remount_controller,
            workspace_lifecycle_admission: Mutex::new(()),
        }
    }

    #[must_use]
    pub(super) fn workspace(&self) -> &Arc<WorkspaceSessionService> {
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
    pub(crate) fn completion_sender(&self) -> &CommandCompletionSender {
        &self.completion_sender
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

    pub(crate) fn begin_workspace_destroy_admission(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> WorkspaceDestroyAdmission<'_> {
        let lifecycle_admission = self.begin_workspace_lifecycle_admission();
        let active_command_session_ids = self
            .process_store()
            .active_command_session_ids_for_workspace_session(workspace_session_id);
        WorkspaceDestroyAdmission {
            active_command_session_ids,
            _lifecycle_admission: lifecycle_admission,
        }
    }
}
