// The run mechanism: handle + events, conversation, provider turn, the
// loop spine, and the injected tool-executor port. The runtime package
// assembles these per run; nothing here reads config or touches disk.
export {
  RUN_FINISHED_DISPOSE_REASON,
  runAgentLoop,
  type AgentLoopContext,
  type TaskRegistryGate,
  type TerminationMode,
  type TurnFacts,
} from "./agent-loop.js";
export {
  Conversation,
  type ConversationRecord,
  type PartialReason,
  type ToolResultBlock,
  type UserMessageOrigin,
} from "./conversation.js";
export {
  EventStream,
  RunHandle,
  type AgentEvent,
  type AgentEventBody,
  type AgentOutcome,
  type AgentRunError,
  type AgentRunHandle,
  type RunHandleDeps,
} from "./run-handle.js";
export type {
  ToolBatchContext,
  ToolExecutor,
  ToolUseBlock,
} from "./tool-executor.js";
export { addUsage, runAssistantTurn, type CompletedTurn, type TurnConfig } from "./turn.js";
