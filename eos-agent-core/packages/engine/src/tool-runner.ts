import type { ContentBlock } from "@eos/contracts";

import type { ToolResultBlock } from "./conversation.js";
import type { AgentEvent } from "./events.js";
import type { ToolOutput, ToolRegistry } from "./tools.js";

type ToolUseBlock = Extract<ContentBlock, { type: "tool_use" }>;

/** Parity with the Rust `MAX_FOREGROUND_TOOL_CONCURRENCY`. */
const MAX_TOOL_CONCURRENCY = 8;

/**
 * Execute one assistant message's `tool_use` batch: fully concurrent under
 * a cap of 8, results assembled in `tool_use` order regardless of
 * completion order. A thrown tool error or an unregistered name becomes an
 * `is_error` result — never an exception, never a sibling cascade. On abort
 * the batch settles immediately: completed calls keep their real results
 * and every other block gets a synthetic `"interrupted"` error result, so
 * the transcript never holds an unanswered `tool_use`.
 */
export async function runToolBatch(
  calls: ToolUseBlock[],
  tools: ToolRegistry,
  signal: AbortSignal,
  emit: (event: AgentEvent) => void,
): Promise<ToolResultBlock[]> {
  const settled = new Array<ToolResultBlock | undefined>(calls.length);
  let cursor = 0;
  const worker = async (): Promise<void> => {
    while (cursor < calls.length && !signal.aborted) {
      const index = cursor;
      cursor += 1;
      const call = calls[index];
      emit({
        type: "tool_execution_started",
        tool_use_id: call.tool_use_id,
        name: call.name,
        input: call.input,
      });
      const output = await executeCall(call, tools, signal);
      // The batch already settled with a synthetic result; drop the straggler
      // so no event lands after run_finished.
      if (isAborted(signal)) return;
      const isError = output.is_error ?? false;
      settled[index] = {
        type: "tool_result",
        tool_use_id: call.tool_use_id,
        content: output.content,
        is_error: isError,
      };
      emit({
        type: "tool_execution_completed",
        tool_use_id: call.tool_use_id,
        name: call.name,
        output: output.content,
        is_error: isError,
      });
    }
  };
  const workers = Promise.all(
    Array.from({ length: Math.min(MAX_TOOL_CONCURRENCY, calls.length) }, () =>
      worker(),
    ),
  );
  await settledOrAborted(workers, signal);
  return calls.map(
    (call, index) =>
      settled[index] ?? {
        type: "tool_result",
        tool_use_id: call.tool_use_id,
        content: "interrupted",
        is_error: true,
      },
  );
}

/** Read through a call so control-flow narrowing never caches `aborted`. */
function isAborted(signal: AbortSignal): boolean {
  return signal.aborted;
}

async function executeCall(
  call: ToolUseBlock,
  tools: ToolRegistry,
  signal: AbortSignal,
): Promise<ToolOutput> {
  const tool = tools.get(call.name);
  if (!tool) return { content: `tool not found: ${call.name}`, is_error: true };
  try {
    return await tool.execute(call.input, { signal });
  } catch (error) {
    return {
      content: error instanceof Error ? error.message : String(error),
      is_error: true,
    };
  }
}

/** Resolve when every worker settles or the signal aborts, whichever first. */
async function settledOrAborted(
  workers: Promise<unknown>,
  signal: AbortSignal,
): Promise<void> {
  if (signal.aborted) return;
  let onAbort: (() => void) | undefined;
  const aborted = new Promise<void>((resolve) => {
    onAbort = () => {
      resolve();
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    await Promise.race([workers, aborted]);
  } finally {
    if (onAbort) signal.removeEventListener("abort", onAbort);
  }
}
