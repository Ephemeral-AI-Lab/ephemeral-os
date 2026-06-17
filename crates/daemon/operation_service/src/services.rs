use std::sync::Arc;

use crate::workspace::WorkspaceManagerService;

#[derive(Clone)]
pub struct OperationServices {
    pub workspace: Arc<WorkspaceManagerService>,
}

impl OperationServices {
    #[must_use]
    pub fn new(workspace: Arc<WorkspaceManagerService>) -> Self {
        Self { workspace }
    }
}
