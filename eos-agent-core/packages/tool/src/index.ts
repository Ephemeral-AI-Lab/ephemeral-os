// The package surface is the authoring contract, the hook protocol, and
// the assembly entry. Pipeline and batch-executor internals (bindTool,
// toolBatchExecutor, the precedence kernel) stay package-private behind
// buildToolExecutor.
export type {
  AgentRunSnapshot,
  ToolCallContext,
  ToolCallMeta,
  ToolDefinition,
  ToolName,
  ToolOutcome,
} from "./contract.js";
export { defineTool, type ToolDefinitionInit } from "./define.js";
export {
  HookConfigEntrySchema,
  HookEventSchema,
  HookOutputSchema,
  type HookCommand,
  type HookConfigEntry,
  type HookEvent,
  type HookOutput,
  type HookPayload,
} from "./hooks/protocol.js";
export { HookEngine } from "./hooks/runner.js";
export { type AgentRunState } from "./run-state.js";
export {
  AGENT_TOOLSET,
  buildToolExecutor,
  type BuildToolExecutorInput,
} from "./toolset.js";
export { backgroundTools } from "./tools/background/index.js";
export { submissionTool } from "./tools/submission/index.js";
