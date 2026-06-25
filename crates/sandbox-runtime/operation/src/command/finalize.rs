use std::sync::Arc;

use sandbox_runtime_namespace_execution::NamespaceExecutionError;

use crate::command::CommandTerminalResult;
use crate::workspace_crate::DestroyWorkspaceRequest;
use crate::workspace_session::{
    WorkspaceSessionError, WorkspaceSessionHandler, WorkspaceSessionService,
};

/// The workspace-completion policy a command applies once its child reaches a
/// terminal state. Closed at these two variants for this rework; `publish`
/// requires a `LayerStackService` collaborator that the command does not yet hold.
pub(crate) enum CommandFinalization {
    KeepSession,
    DestroyOneShot(Box<WorkspaceSessionHandler>),
}

impl CommandFinalization {
    fn apply(self, workspace: &WorkspaceSessionService) -> Result<(), WorkspaceSessionError> {
        match self {
            Self::KeepSession => Ok(()),
            Self::DestroyOneShot(handler) => workspace
                .destroy_session(*handler, DestroyWorkspaceRequest::default())
                .map(|_| ()),
        }
    }
}

/// Assemble the engine `on_complete` closure: apply the workspace completion
/// policy once the child reaches a terminal state. Teardown errors stay internal
/// to finalization and never surface in the command result.
pub(crate) fn build_on_complete(
    finalization: CommandFinalization,
    workspace: Arc<WorkspaceSessionService>,
) -> impl FnOnce(&Result<CommandTerminalResult, NamespaceExecutionError>) + Send + 'static {
    move |_result| {
        let _ = finalization.apply(&workspace);
    }
}
