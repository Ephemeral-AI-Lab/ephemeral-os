//! Public non-blocking agent-loop API and internal loop executor.

mod agent_loop_executor;
mod agent_loop_state;
mod contracts;
mod launcher;

pub use contracts::{
    AgentLoopToolRegistryBuildInput, AgentLoopToolRegistryFactory, BackgroundSessionInputs,
    ExecutionMetadataBuildInput, ToolCallHookStores, ToolExecutionMetadataReader,
};
pub(crate) use eos_types::{
    AgentLoopMessage, AgentLoopOutcome, AgentLoopOutcomeKind, StartAgentLoopRequest,
};
pub use launcher::TokioAgentLoopLauncher;

pub(crate) use agent_loop_executor::{AgentLoopExecutor, AgentLoopExecutorInput};
pub(crate) use agent_loop_state::{AgentLoopRunServices, AgentLoopState};
pub(crate) use contracts::tool_result_payload;
pub(crate) use launcher::AgentLoopCancelSignal;
pub(crate) use launcher::AgentLoopProviderStream;
