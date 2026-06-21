use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use crate::command::{CommandLaunchDriver, CommandProcessStore, RealCommandLaunchDriver};
use crate::layerstack::LayerStackService;
use crate::workspace_remount::{ProcProcessGroupController, ProcessGroupController};
use crate::workspace_session::WorkspaceSessionService;

pub struct CommandOperationService {
    workspace: Arc<WorkspaceSessionService>,
    layerstack: Option<Arc<LayerStackService>>,
    config: ::sandbox_runtime_command::CommandConfig,
    process_store: Arc<CommandProcessStore>,
    launch_driver: Arc<dyn CommandLaunchDriver>,
    remount_controller: Arc<dyn ProcessGroupController>,
    remount_admission: Mutex<()>,
}

impl CommandOperationService {
    #[must_use]
    pub fn new(
        workspace: Arc<WorkspaceSessionService>,
        config: ::sandbox_runtime_command::CommandConfig,
    ) -> Self {
        Self::from_parts(
            workspace,
            None,
            config,
            Arc::new(RealCommandLaunchDriver),
            Arc::new(ProcProcessGroupController),
        )
    }

    #[must_use]
    pub fn new_with_layerstack(
        workspace: Arc<WorkspaceSessionService>,
        layerstack: Arc<LayerStackService>,
        config: ::sandbox_runtime_command::CommandConfig,
    ) -> Self {
        Self::from_parts(
            workspace,
            Some(layerstack),
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
            None,
            config,
            launch_driver,
            Arc::new(ProcProcessGroupController),
        )
    }

    #[doc(hidden)]
    #[must_use]
    pub fn with_launch_driver_and_layerstack_for_test(
        workspace: Arc<WorkspaceSessionService>,
        layerstack: Arc<LayerStackService>,
        config: ::sandbox_runtime_command::CommandConfig,
        launch_driver: Arc<dyn CommandLaunchDriver>,
    ) -> Self {
        Self::from_parts(
            workspace,
            Some(layerstack),
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
        Self::from_parts(workspace, None, config, launch_driver, remount_controller)
    }

    fn from_parts(
        workspace: Arc<WorkspaceSessionService>,
        layerstack: Option<Arc<LayerStackService>>,
        config: ::sandbox_runtime_command::CommandConfig,
        launch_driver: Arc<dyn CommandLaunchDriver>,
        remount_controller: Arc<dyn ProcessGroupController>,
    ) -> Self {
        Self {
            workspace,
            layerstack,
            config,
            process_store: Arc::new(CommandProcessStore::new()),
            launch_driver,
            remount_controller,
            remount_admission: Mutex::new(()),
        }
    }

    #[must_use]
    pub(crate) fn workspace(&self) -> &Arc<WorkspaceSessionService> {
        &self.workspace
    }

    #[must_use]
    pub(crate) fn layerstack(&self) -> Option<&Arc<LayerStackService>> {
        self.layerstack.as_ref()
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

    pub(crate) fn lock_remount_admission(&self) -> MutexGuard<'_, ()> {
        self.remount_admission
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }
}
