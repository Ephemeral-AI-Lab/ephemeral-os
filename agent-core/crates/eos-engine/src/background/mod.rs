//! Engine background supervisor: subagent driver, command-session supervision,
//! and the command-completion heartbeat.

mod command_session;
mod factory;
mod handle;
mod heartbeat;
mod parent_exit;
mod subagent;
mod supervisor;

pub use command_session::CommandSessionRecord;
pub use factory::BackgroundSupervisorFactory;
pub use handle::BackgroundSupervisorHandle;
pub use heartbeat::spawn_command_completion_heartbeat;
pub(crate) use parent_exit::BackgroundRunFinalizer;
pub use supervisor::{
    BackgroundTaskStatus, BackgroundTaskSupervisor, SubagentRecord, WorkflowBackgroundRecord,
};
