use std::sync::Arc;

use crate::command::{
    CommandCallContext, CommandProcessStore, CommandRegistry, CommandServiceError, CommandYield,
    ExecCommandInput,
};
use crate::workspace_manager::{WorkspaceManagerService, WorkspaceSessionHandler};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CommandFinalizationOptions {
    pub one_shot_capture: layerstack::service::BoundedCaptureOptions,
    pub one_shot_publish: layerstack::CommitOptions,
}

pub struct CommandOperationService {
    workspace: Arc<WorkspaceManagerService>,
    config: ::command::CommandConfig,
    registry: Arc<CommandRegistry>,
    process_store: Arc<CommandProcessStore>,
    finalization_options: CommandFinalizationOptions,
}

impl CommandOperationService {
    #[must_use]
    pub fn new(workspace: Arc<WorkspaceManagerService>, config: ::command::CommandConfig) -> Self {
        Self::with_finalization_options(workspace, config, CommandFinalizationOptions::default())
    }

    #[must_use]
    pub fn with_finalization_options(
        workspace: Arc<WorkspaceManagerService>,
        config: ::command::CommandConfig,
        finalization_options: CommandFinalizationOptions,
    ) -> Self {
        Self {
            workspace,
            config,
            registry: Arc::new(CommandRegistry::new()),
            process_store: Arc::new(CommandProcessStore::new()),
            finalization_options,
        }
    }

    #[must_use]
    pub fn finalization_options(&self) -> &CommandFinalizationOptions {
        &self.finalization_options
    }

    #[must_use]
    pub fn workspace(&self) -> &Arc<WorkspaceManagerService> {
        &self.workspace
    }

    #[must_use]
    pub fn config(&self) -> &::command::CommandConfig {
        &self.config
    }

    #[must_use]
    pub fn registry(&self) -> &Arc<CommandRegistry> {
        &self.registry
    }

    #[must_use]
    pub fn process_store(&self) -> &Arc<CommandProcessStore> {
        &self.process_store
    }

    pub fn exec_command(
        &self,
        _input: ExecCommandInput,
        _workspace: Option<WorkspaceSessionHandler>,
        _context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        Err(CommandServiceError::NotImplemented)
    }
}
