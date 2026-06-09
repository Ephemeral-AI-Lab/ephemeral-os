//! eos-types — shared id, timestamp, clock, json, errors, and state contracts.
//!
//! This is the upstream contract crate of the agent-core dependency DAG. It
//! holds the typed string ids, the [`UtcDateTime`] wrapper, the [`Clock`]
//! seam, the transitional [`JsonObject`] alias, the minimal [`CoreError`], and
//! the persisted DTO/store contracts shared across runtime, engine, workflow,
//! tools, and database crates. It deliberately holds no SQL, HTTP, provider, or
//! orchestration behavior.
//!
//! The public surface is re-exported flatly, so consumers write
//! `use eos_types::{AgentRunId, UtcDateTime, Clock, JsonObject};`.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod agent;
pub mod agent_loop;
mod contracts;
mod error;
mod frontmatter;
mod ids;
mod llm;
mod models;
mod state;
mod stores;
mod time;

pub use agent::{
    AgentDefinition, AgentName, AgentNameError, AgentRegistry, AgentRegistryBuilder, AgentType,
};
pub use agent_loop::{
    AgentLoopCancellation, AgentLoopCancellationHandle, AgentLoopCompletion, AgentLoopLauncher,
    AgentLoopMessage, AgentLoopOutcome, AgentLoopOutcomeKind, StartAgentLoopRequest,
    StartedAgentLoop,
};
pub use contracts::{
    format_record_dir, AgentCoreCancellationApi, AgentRunApi, AgentRunError, AgentRunOutcome,
    AgentRunRecordDir, AgentRunRecordIndex, AgentRunRecordTarget, AgentRunRuntimeSnapshot,
    AgentRunStatus, CancelError, CreatedAgentRun, OpenDelegatedWorkflow, SpawnAgentRequest,
    StartWorkflowRequest, StartedWorkflow, SubmissionAck, TerminalWorkflow, WorkflowApi,
    WorkflowApiError, WorkflowAttemptSubmissionApi, WorkflowCoordinates, WorkflowAgentRole,
    WorkflowTerminalStatus,
};
pub use error::CoreError;
pub use frontmatter::parse_markdown_frontmatter;
pub use ids::{
    AgentRunId, AttemptId, CommandSessionId, InvocationId, IterationId, RequestId, SandboxId,
    ToolUseId, WorkflowId,
};
pub use llm::{ContentBlock, Message, MessageRole, ToolSpec, DEFAULT_MAX_TOKENS};
pub use models::{ConfigError, JsonObject, ModelRegistrationConfig, ModelsConfig};
pub use state::{
    AdvisorVerdict, AgentRun, Attempt, AttemptBudget, AttemptClosure, AttemptExecutionTree,
    AttemptFailReason, AttemptOutcome, AttemptStage, AttemptState, AttemptStatus,
    BackgroundSessionCounts, DeferredGoal, ExecutionNode, Iteration, IterationCreationReason,
    IterationOutcome, IterationStatus, ModelRegistration, PlanId, PlanOutcomeSubmission,
    PlannerOutcome, Request, RequestStatus, RunningRequestAgentRun, SubmissionStatus,
    SubmissionOutcome, ExecutionStatus, WorkItemId, WorkItemSpec, WorkerOutcome,
    WorkerOutcomeSubmission, Workflow, WorkflowOutcome, WorkflowStatus, NO_OUTCOME,
};
pub use stores::{
    AgentRunStore, AttemptStore, IterationStore, ModelStore, RequestStore, Sealed, StoreError,
    WorkflowStore,
};
pub use time::{Clock, SystemClock, TestClock, UtcDateTime};
