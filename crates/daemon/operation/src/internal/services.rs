use std::sync::Arc;

use crate::command::CommandOperationService;

#[derive(Clone)]
pub struct DaemonOperations {
    pub command: Arc<CommandOperationService>,
}

impl DaemonOperations {
    #[must_use]
    pub fn new(command: Arc<CommandOperationService>) -> Self {
        Self { command }
    }
}
