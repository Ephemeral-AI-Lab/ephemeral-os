//! Engine-local background session accounting for one agent run.

mod command_session;
mod notification;
mod session_runtime;
mod subagent_session;
mod workflow_session;

pub use notification::{BackgroundCompletion, BackgroundNotificationEmitter};
pub use session_runtime::{
    BackgroundSessionRuntime, BackgroundSessionStatus, BackgroundSessionTeardown,
};
