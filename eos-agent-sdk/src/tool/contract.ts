import type {
  AgentRunId,
  JsonObject,
  JsonValue,
  Message,
  ToolUseId,
} from "../contracts/index.js";
import type { BackgroundTaskSupervisor } from "../background/index.js";
import type { Notifier } from "../notification/index.js";
import type { z } from "zod";

/**
 * What a tool execution yields: an output the engine stringifies exactly
 * once, or a model-facing error. `metadata` is records/observability only.
 */
export type ToolResult =
  | { output: JsonValue; metadata?: JsonObject }
  | { error: string };

/**
 * What `execute()` receives: per-call facts plus the run-scoped
 * capabilities — the same supervisor and notifier instances the handle
 * exposes. `llmMessages` is a read-only snapshot taken at batch start: a
 * deliberate capability (transcript-aware tools); hosts that run
 * third-party tools should treat it as part of their trust decision.
 */
export interface ToolCallContext {
  runId: AgentRunId;
  /** Event/record correlation; idempotency keying. */
  toolUseId: ToolUseId;
  /** Aborts on `interrupt()`. */
  signal: AbortSignal;
  llmMessages: readonly Message[];
  backgroundTaskSupervisor: BackgroundTaskSupervisor;
  notifier: Notifier;
}

/**
 * The authoring surface — name, docstring, input contract, behavior.
 * There is no flag metadata: foreground / background / yield are runtime
 * patterns of `execute`, and the engine never branches on them. Build
 * instances through `defineTool`.
 */
export interface ToolDefinition<I = unknown> {
  readonly name: string;
  readonly description: string;
  /** Input contract; also the wire spec source. */
  readonly input: z.ZodType<I>;
  execute(input: I, ctx: ToolCallContext): Promise<ToolResult>;
}
