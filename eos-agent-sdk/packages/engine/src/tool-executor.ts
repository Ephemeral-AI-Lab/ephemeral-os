import type {
  ContentBlock,
  Message,
  ToolCallResult,
  ToolSpec,
} from "@eos/contracts";
import type { AgentEventBody } from "./run-handle.js";

/** A model-emitted `tool_use` block, the unit of batch dispatch. */
export type ToolUseBlock = Extract<ContentBlock, { type: "tool_use" }>;

/** Per-batch facts the loop hands the executor. */
export interface ToolBatchContext {
  /** Aborts on `interrupt()`. */
  signal: AbortSignal;
  emit: (event: AgentEventBody) => void;
  /** Read-only conversation snapshot taken at batch start. */
  llmMessages: readonly Message[];
}

/**
 * The engine's ONE piece of tool knowledge - an injected port. Registry,
 * concurrency cap, terminal ordering, hooks, and the per-call pipeline all
 * live behind it (in `@eos/tool`). The engine keeps only the invariant it
 * cannot delegate: after `executeBatch` returns, any unanswered
 * `tool_use_id` is filled with a synthetic error result so provider-history
 * validity never depends on executor correctness.
 */
export interface ToolExecutor {
  specs(): ToolSpec[];
  executeBatch(
    calls: ToolUseBlock[],
    batch: ToolBatchContext,
  ): Promise<ToolCallResult[]>;
}
