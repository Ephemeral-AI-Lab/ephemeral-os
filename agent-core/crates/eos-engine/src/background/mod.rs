//! Engine background supervisor: subagent driver, command-session supervision,
//! and the command-completion heartbeat.

mod command_session;
mod handle;
mod heartbeat;
mod subagent;
mod supervisor;

pub use command_session::CommandSessionRecord;
pub use handle::BackgroundSupervisorHandle;
pub use heartbeat::spawn_command_completion_heartbeat;
pub use supervisor::{
    BackgroundTaskStatus, BackgroundTaskSupervisor, SubagentRecord, WorkflowBackgroundRecord,
};
