//! Engine background supervisor: subagent driver, command-session supervision,
//! and the command-completion heartbeat.

mod command_session;
mod heartbeat;
mod subagent;
mod supervisor;

pub use command_session::CommandSessionRecord;
pub use heartbeat::spawn_command_completion_heartbeat;
pub use supervisor::{
    BackgroundSupervisorHandle, BackgroundTaskKind, BackgroundTaskRecord, BackgroundTaskStatus,
    BackgroundTaskSupervisor, StopMode,
};
