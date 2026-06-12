// eos-agent-sdk — the complete public surface (spec §3). Three construction
// values and types only: no tool implementations, no Zod schemas, no
// subprocess execution, no filesystem path but `recordsDir`.

// ── construction ────────────────────────────────────────────────
export {
  createAgentSdk,
  type Agent,
  type AgentSdk,
  type AgentSdkConfig,
  type AgentSpec,
} from "@eos/runtime";
export type {
  LlmClientConfig,
  LlmClientProfile,
  LlmRef,
} from "@eos/runtime";

// ── agents & runs ───────────────────────────────────────────────
export type {
  AgentEvent,
  AgentOutcome,
  AgentRunError,
  AgentRunHandle,
  TurnFacts,
} from "@eos/engine";

// ── run-scoped capabilities ─────────────────────────────────────
export type {
  BackgroundTask,
  BackgroundTaskCompletionContext,
  BackgroundTaskOutcome,
  BackgroundTaskRow,
  BackgroundTaskSupervisor,
} from "@eos/background";
export type { Notifier } from "@eos/notification";

// ── authoring ───────────────────────────────────────────────────
export {
  createAgentOutcomeFn,
  defineTool,
  type AgentOutcomeFn,
  type HookDecision,
  type HookEntry,
  type HookMatcher,
  type SubmitCtx,
  type ToolCallContext,
  type ToolCallFacts,
  type ToolDefinition,
  type ToolDefinitionInit,
  type ToolResult,
} from "@eos/tool";

// ── exported types (no values, no schemas) ──────────────────────
export type {
  AgentRunId,
  BackgroundTaskId,
  ContentBlock,
  JsonObject,
  JsonValue,
  Message,
  ToolUseId,
  UserMessage,
} from "@eos/contracts";
export type {
  ProviderConnection,
  ReasoningEffort,
  UsageSnapshot,
} from "@eos/llm-client";
