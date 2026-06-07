//! Engine-local background session accounting for one agent run.

mod factory;
mod notification;
mod parent_exit;
mod session_managers;
mod session_runtime;

pub use factory::BackgroundSessionFactory;
pub use notification::{BackgroundCompletion, BackgroundNotificationEmitter};
pub(crate) use parent_exit::BackgroundRunFinalizer;
pub use session_managers::BackgroundSessionStatus;
pub use session_runtime::BackgroundSessionService;
