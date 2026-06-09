//! Cross-crate lifecycle contracts.
//!
//! These modules hold owner-neutral behavior traits and passive DTOs shared
//! across sibling crates. Keeping them in `eos-types` avoids dependency cycles:
//! engine, workflow, tools, and agent-run can all consume the contracts without
//! depending on each other's concrete implementations.

mod agent_run;
mod cancellation;
mod record;
mod workflow;

pub use agent_run::{
    AgentRunApi, AgentRunError, AgentRunOutcome, AgentRunRuntimeSnapshot, AgentRunStatus,
    ParentAgentRunAnchor, SpawnAgentRequest, SpawnAgentTarget,
};
pub use cancellation::{AgentCoreCancellationApi, CancelError};
pub use record::{
    format_record_dir, AgentRunRecordDir, AgentRunRecordIndex, AgentRunRecordTarget,
    CreatedTaskAgentRun, ParentedAgentRunKind, TaskAgentRunKind, TaskExecutionIndex,
    WorkflowCoordinates, WorkflowNodeId, WorkflowTaskRole,
};
pub use workflow::{
    OpenDelegatedWorkflow, PlanReducer, PlanTask, PlannerPlan, StartWorkflowRequest,
    StartedWorkflow, SubmissionAck, TerminalWorkflow, WorkflowApi, WorkflowApiError,
    WorkflowAttemptSubmissionApi, WorkflowTerminalStatus,
};
