import type { ToolCallResult, ToolSpec, ToolUseId } from "@eos/contracts";
import type { AgentEvent, ToolExecutor, ToolUseBlock } from "@eos/engine";

import { projectContent, type BoundTool } from "./pipeline.js";
import { snapshotRunState, type AgentRunState } from "./run-state.js";

/** Parity with the Rust `MAX_FOREGROUND_TOOL_CONCURRENCY`. */
const MAX_TOOL_CONCURRENCY = 8;

export interface ToolBatchExecutorInput {
  runState: AgentRunState;
  /** Already bound and deterministically ordered by the assembler. */
  tools: BoundTool[];
}

/**
 * The `ToolExecutor` the engine injects. Per-turn `specs()` filters by
 * workspace mode; `executeBatch` keeps the Phase 03 runner semantics -
 * fully concurrent under a cap of 8, results in `tool_use` order, thrown
 * errors and unknown names mapped to `is_error` results, abort settling
 * with straggler-emit suppression - and adds the terminal-solo policy: a
 * terminal call with any sibling rejects the WHOLE batch undispatched.
 */
export function toolBatchExecutor(input: ToolBatchExecutorInput): ToolExecutor {
  const { runState, tools } = input;
  const byName = new Map(tools.map((tool) => [tool.definition.name as string, tool]));
  return {
    specs(): ToolSpec[] {
      return tools
        .filter(
          (tool) =>
            !runState.workspace.isIsolated ||
            tool.definition.availableInIsolatedWorkspace,
        )
        .map((tool) => tool.definition.spec);
    },
    async executeBatch(
      calls: ToolUseBlock[],
      signal: AbortSignal,
      emit: (event: AgentEvent) => void,
    ): Promise<ToolCallResult[]> {
      // One snapshot per batch: every sibling's meta is built from it, so
      // a mid-batch workspace flip applies at the next turn boundary.
      const run = snapshotRunState(runState);
      const rejection = terminalBatchRejection(calls, byName);
      if (rejection !== undefined) {
        const at = Date.now();
        return calls.map((call) => ({
          tool_use_id: call.tool_use_id,
          content: rejection,
          is_error: true,
          is_terminal: false,
          tool_start_time: at,
          tool_end_time: at,
        }));
      }
      const settled = new Array<ToolCallResult | undefined>(calls.length);
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
          const result = await executeCall(call, byName, run, signal);
          // The batch already settled with a synthetic result; drop the
          // straggler so no event lands after run_finished.
          if (isAborted(signal)) return;
          settled[index] = result;
          emit({
            type: "tool_execution_completed",
            tool_use_id: call.tool_use_id,
            name: call.name,
            output: projectContent(result.content),
            is_error: result.is_error,
            is_terminal: result.is_terminal,
            tool_start_time: result.tool_start_time,
            tool_end_time: result.tool_end_time,
            ...(result.metadata !== undefined && { metadata: result.metadata }),
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
        (call, index) => settled[index] ?? interrupted(call.tool_use_id),
      );
    },
  };
}

/**
 * Terminal-solo policy (Rust `reject_terminal_batch` parity): a batch with
 * a terminal call plus any sibling rejects every call; a solo terminal
 * call dispatches normally.
 */
function terminalBatchRejection(
  calls: ToolUseBlock[],
  byName: Map<string, BoundTool>,
): string | undefined {
  if (calls.length <= 1) return undefined;
  const flagged = [
    ...new Set(
      calls
        .filter((call) => byName.get(call.name)?.definition.terminal)
        .map((call) => `\`${call.name}\``),
    ),
  ].sort();
  if (flagged.length === 0) return undefined;
  return `terminal tool ${flagged.join(", ")} must be called alone; the whole batch was rejected without dispatching`;
}

async function executeCall(
  call: ToolUseBlock,
  byName: Map<string, BoundTool>,
  run: ReturnType<typeof snapshotRunState>,
  signal: AbortSignal,
): Promise<ToolCallResult> {
  const tool = byName.get(call.name);
  if (!tool) {
    const at = Date.now();
    return {
      tool_use_id: call.tool_use_id,
      content: `tool not found: ${call.name}`,
      is_error: true,
      is_terminal: false,
      tool_start_time: at,
      tool_end_time: at,
    };
  }
  try {
    return { tool_use_id: call.tool_use_id, ...(await tool.run(call, run, signal)) };
  } catch (error) {
    // The pipeline never throws by contract; this keeps a buggy tool from
    // cascading into siblings all the same.
    const at = Date.now();
    return {
      tool_use_id: call.tool_use_id,
      content: error instanceof Error ? error.message : String(error),
      is_error: true,
      is_terminal: false,
      tool_start_time: at,
      tool_end_time: at,
    };
  }
}

function interrupted(toolUseId: ToolUseId): ToolCallResult {
  const at = Date.now();
  return {
    tool_use_id: toolUseId,
    content: "interrupted",
    is_error: true,
    is_terminal: false,
    tool_start_time: at,
    tool_end_time: at,
  };
}

/** Read through a call so control-flow narrowing never caches `aborted`. */
function isAborted(signal: AbortSignal): boolean {
  return signal.aborted;
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
