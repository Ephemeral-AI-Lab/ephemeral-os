import {
  zodIssueSummary,
  type JsonObject,
  type ToolSpec,
} from "../contracts/index.js";
import type { ToolBatchContext, ToolUseBlock } from "../engine/index.js";
import { z } from "zod";

import type { AgentOutcomeBinding, SubmitVerdict } from "./outcome.js";
import {
  callFacts,
  rejectedResult,
  type BoundTool,
  type PipelineResult,
  type RunScope,
} from "./pipeline.js";

/**
 * The submission gate the runtime closes over the run's live state. The
 * gate is SDK-internal mechanism, not a configurable hook — hosts tune
 * nothing here.
 */
export interface TerminalGate {
  /** Model-facing blocker descriptions; an empty array means open. */
  blockers(): string[];
  /** Latch steers off while `onSubmit` is in flight. */
  beginFinishing(): void;
  /** A rejection reopens the run. */
  cancelFinishing(): void;
}

export interface TerminalBinding<T> {
  bound: BoundTool;
  /** The submission accepted in the current batch, if any (read-once). */
  takeAccepted: () => { value: T } | undefined;
}

/**
 * Bind the minted terminal contract as an ordinary-looking tool whose
 * successful result ends the run. Order per call: schema -> gate -> pre
 * hooks -> post hooks (on the provisional result) -> `onSubmit`. Hooks
 * run BEFORE the handler so a hook deny — like a handler reject — costs
 * the host nothing; once `onSubmit` accepts, nothing can stop the finish,
 * so the accepted value and host state cannot diverge (single mutator).
 */
export function bindTerminalTool<T>(
  fn: AgentOutcomeBinding<T>,
  scope: RunScope,
  gate: TerminalGate,
): TerminalBinding<T> {
  let accepted: { value: T } | undefined;
  const spec: ToolSpec = {
    name: fn.name,
    description: fn.description,
    input_schema: z.toJSONSchema(fn.schema) as JsonObject,
  };

  const run = async (
    call: ToolUseBlock,
    batch: ToolBatchContext,
  ): Promise<PipelineResult> => {
    if (batch.signal.aborted) return rejectedResult("interrupted");

    const parsed = fn.schema.safeParse(call.input);
    if (!parsed.success) {
      return rejectedResult(
        `invalid submission for ${fn.name}: ${zodIssueSummary(parsed.error)}`,
      );
    }

    // The gate (§4.1): evaluated after every sibling call has resolved
    // (the executor orders the terminal call last). The denial reason
    // enumerates the blockers so the model can act on them.
    const blockers = gate.blockers();
    if (blockers.length > 0) {
      return rejectedResult(`submission denied: ${blockers.join("; ")}`);
    }

    const facts = callFacts(scope.runId, call, fn.name);
    const pre = await scope.hooks.preToolUse(facts);
    if (pre.decision === "deny") return rejectedResult(pre.reason);
    const post = await scope.hooks.postToolUse(facts, { output: call.input });
    if (post.decision === "deny") return rejectedResult(post.reason);

    gate.beginFinishing();
    const startedAt = Date.now();
    let verdict: SubmitVerdict<T>;
    try {
      verdict = await fn.onSubmit(parsed.data, {
        runId: scope.runId,
        submissionId: call.tool_use_id,
      });
    } catch (error) {
      gate.cancelFinishing();
      const endedAt = Date.now();
      return {
        content: `onSubmit failed: ${error instanceof Error ? error.message : String(error)}`,
        is_error: true,
        is_terminal: false,
        tool_start_time: startedAt,
        tool_end_time: endedAt,
      };
    }
    const endedAt = Date.now();
    if ("reject" in verdict) {
      // A rejection costs the host nothing: returned to the live model as
      // a tool error; the run continues.
      gate.cancelFinishing();
      return {
        content: verdict.reject,
        is_error: true,
        is_terminal: false,
        tool_start_time: startedAt,
        tool_end_time: endedAt,
      };
    }
    accepted = { value: verdict.accept };
    return {
      content: "submission accepted",
      is_error: false,
      is_terminal: true,
      tool_start_time: startedAt,
      tool_end_time: endedAt,
    };
  };

  return {
    bound: { name: fn.name, spec, run },
    takeAccepted: () => {
      const taken = accepted;
      accepted = undefined;
      return taken;
    },
  };
}
