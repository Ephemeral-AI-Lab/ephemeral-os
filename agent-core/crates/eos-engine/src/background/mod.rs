//! Engine background supervisor: per-agent-run subagent / workflow / command
//! lanes, the command-completion heartbeat (owned by the command lane), and
//! parent-exit / cancellation teardown.

mod command_session;
mod factory;
mod handle;
mod lanes;
mod notifications;
mod parent_exit;
mod subagent;
mod supervisor;
mod workflow_poll;

pub use factory::BackgroundSupervisorFactory;
pub use handle::BackgroundSupervisorHandle;
pub use lanes::{
    BackgroundTaskStatus, CommandSessionHandle, CommandSessionRecord, SubagentHandle,
    SubagentRecord, WorkflowBackgroundRecord, WorkflowHandle,
};
pub use notifications::{BackgroundCompletion, BackgroundNotificationEmitter};
pub(crate) use parent_exit::BackgroundRunFinalizer;
pub use supervisor::BackgroundTaskSupervisor;
