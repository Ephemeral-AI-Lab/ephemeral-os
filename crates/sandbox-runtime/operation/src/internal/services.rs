use std::sync::Arc;

use crate::command::CommandOperationService;

#[derive(Clone)]
pub struct SandboxDaemonOperations {
    pub command: Arc<CommandOperationService>,
}

impl SandboxDaemonOperations {
    #[must_use]
    pub fn new(command: Arc<CommandOperationService>) -> Self {
        Self { command }
    }
}
