//! Compatibility re-exports for shared port contracts.
//!
//! The contract definitions live in `eos-ports`. This module keeps the old
//! `eos_tools::ports::*` path available while runtime/tool call sites migrate to
//! the narrower port crate.

pub use eos_ports::{
    AgentRunServicePort, AttemptSubmissionPort, BackgroundSessionCounts, CancelPort,
    CancelableResource, CancelledSubagent, CommandServicePort, CommandSessionPort,
    NotificationSink, OutstandingWorkflow, PlanReducer, PlanTask, PlannerPlan, Sealed,
    StartSubagentRunOutcome, StartSubagentRunRequest, StartWorkflowRequest, StartedSubagentRun,
    StartedWorkflow, StartedWorkflowSession, SubagentLaunchRejection, SubagentProgress,
    SubagentSessionPort, SubagentSessionStatus, SubmissionAck, SystemNotification,
    TerminalAgentRun, TerminalWorkflow, WorkflowServicePort, WorkflowSessionPort,
};
