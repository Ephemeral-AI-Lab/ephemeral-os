import type { JsonObject, ToolSpec } from "@eos/contracts";

/** What a tool execution may observe; aborts via the run's one signal. */
export interface ToolContext {
  signal: AbortSignal;
}

/** What a tool execution yields back to the model. */
export interface ToolOutput {
  content: string;
  /** Defaults to false. */
  is_error?: boolean;
}

/**
 * The narrow tool seam the loop needs — not a tool framework. A future
 * framework phase grows this contract additively.
 */
export interface ToolDefinition {
  /** Neutral declaration sent to the model; `spec.name` keys the registry. */
  spec: ToolSpec;
  /**
   * Recorded on the contract now so per-call concurrency partitioning can
   * arrive without changing the execution model under already-written
   * tools; this phase's dispatcher ignores it (default true: every batch
   * runs fully concurrent).
   */
  isConcurrencySafe?(input: JsonObject): boolean;
  execute(input: JsonObject, ctx: ToolContext): Promise<ToolOutput>;
}

/** Tools offered to one run, keyed by `spec.name`. */
export type ToolRegistry = ReadonlyMap<string, ToolDefinition>;
