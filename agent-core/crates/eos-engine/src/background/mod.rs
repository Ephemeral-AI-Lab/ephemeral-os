//! Engine background policy, dispatch, supervisor, and command-session
//! heartbeat.

mod command_session;
mod dispatch;
mod heartbeat;
mod policy;
mod supervisor;

pub use command_session::CommandSessionRecord;
pub use dispatch::launch_background_tool;
pub use heartbeat::spawn_command_completion_heartbeat;
pub use policy::{is_engine_background_tool, needs_background_manager};
pub use supervisor::{
    BackgroundTaskKind, BackgroundTaskRecord, BackgroundTaskStatus, BackgroundTaskSupervisor,
    SharedSubagentSupervisor, StopMode,
};
