// The package surface: the authoring contract (defineTool), the terminal
// contract factory (createAgentOutcomeFn), the callback hook engine, and
// the executor assembly. Pipeline and batch-executor internals stay
// package-private behind buildToolExecutor. The package ships ZERO tool
// implementations — every tool is host-authored.
export type {
  ToolCallContext,
  ToolDefinition,
  ToolResult,
} from "./contract.js";
export { defineTool, type ToolDefinitionInit } from "./define.js";
export {
  HookEngine,
  type HookDecision,
  type HookEntry,
  type HookMatcher,
  type ToolCallFacts,
} from "./hooks.js";
export {
  createAgentOutcomeFn,
  unwrapAgentOutcomeFn,
  type AgentOutcomeBinding,
  type AgentOutcomeFn,
  type SubmitCtx,
  type SubmitVerdict,
} from "./outcome.js";
export type { RunScope } from "./pipeline.js";
export type { TerminalGate } from "./terminal.js";
export {
  buildToolExecutor,
  type BuildToolExecutorInput,
  type BuiltToolExecutor,
} from "./toolset.js";
