//! Engine-local background session accounting for one agent run.

mod notification;
mod session_managers;
mod session_runtime;

pub use notification::{BackgroundCompletion, BackgroundNotificationEmitter};
pub use session_managers::BackgroundSessionStatus;
pub(crate) use session_runtime::BackgroundSessionFinalizer;
pub use session_runtime::{BackgroundSessionService, BackgroundTeardownPort};
