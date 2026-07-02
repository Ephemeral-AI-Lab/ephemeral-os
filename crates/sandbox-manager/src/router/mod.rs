mod dispatch;
mod forward;

pub(crate) use forward::forward_sandbox_request;

use std::sync::Arc;

use crate::ManagerServices;

#[derive(Clone)]
pub struct SandboxManagerRouter {
    services: Arc<ManagerServices>,
}

impl SandboxManagerRouter {
    #[must_use]
    pub const fn new(services: Arc<ManagerServices>) -> Self {
        Self { services }
    }
}
