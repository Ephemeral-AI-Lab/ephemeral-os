//! Engine-local background session accounting for one agent run.

mod background_session_manager;
mod notification;

pub use background_session_manager::BackgroundSessionStatus;
pub use background_session_manager::{BackgroundManagers, BackgroundTeardownService};
pub use notification::{BackgroundCompletion, BackgroundNotificationEmitter};
