import type { AgentRunId, JsonObject, ToolUseId } from "../contracts/index.js";
import type { TurnFacts } from "../engine/index.js";
import type { Notifier } from "../notification/index.js";

import type { ToolResult } from "./contract.js";

/** Exact tool name; absent matches all tools. */
export interface HookMatcher {
  toolName?: string;
}

/** Pre/post hooks speak only through this — one channel per signal. */
export type HookDecision =
  | { decision: "passthrough" }
  | { decision: "deny"; reason: string };

/**
 * What pre/post hooks receive — per-call facts only, no conversation
 * access. A submission-vetting hook judges the payload alone; terminal
 * payload schemas must be self-contained enough to vet. This is a
 * designed constraint, not an omission.
 */
export interface ToolCallFacts {
  readonly runId: AgentRunId;
  readonly toolUseId: ToolUseId;
  readonly toolName: string;
  readonly input: JsonObject;
}

/**
 * The one extension engine: callbacks on three events, matched by tool
 * name. `preToolUse` deny means the call never executes; `postToolUse`
 * deny replaces the executed result; `turnBoundary` observes turn facts
 * and may publish through the provided notifier — it returns nothing.
 */
export type HookEntry =
  | {
      event: "preToolUse";
      matcher?: HookMatcher;
      run: (call: ToolCallFacts) => HookDecision | Promise<HookDecision>;
    }
  | {
      event: "postToolUse";
      matcher?: HookMatcher;
      run: (
        call: ToolCallFacts,
        result: ToolResult,
      ) => HookDecision | Promise<HookDecision>;
    }
  | {
      event: "turnBoundary";
      run: (
        turn: TurnFacts,
        ctx: { notifier: Notifier; runId: AgentRunId },
      ) => void | Promise<void>;
    };

/**
 * Callback dispatch over one run's merged entries (globals first, then
 * per-agent). Pre/post entries matching the call run in parallel; any
 * deny wins and reasons join. A throwing pre/post hook resolves as deny
 * with the thrown message (fail-closed; a host wanting fail-open catches
 * inside its callback). A throwing turnBoundary hook is recorded and
 * skipped. A broken hook never wedges a batch.
 */
export class HookEngine {
  readonly #pre: Extract<HookEntry, { event: "preToolUse" }>[];
  readonly #post: Extract<HookEntry, { event: "postToolUse" }>[];
  readonly #boundary: Extract<HookEntry, { event: "turnBoundary" }>[];

  constructor(entries: readonly HookEntry[]) {
    this.#pre = entries.filter((entry) => entry.event === "preToolUse");
    this.#post = entries.filter((entry) => entry.event === "postToolUse");
    this.#boundary = entries.filter((entry) => entry.event === "turnBoundary");
  }

  async preToolUse(call: ToolCallFacts): Promise<HookDecision> {
    return combine(
      await Promise.all(
        this.#pre
          .filter((entry) => matches(entry.matcher, call.toolName))
          .map((entry) => guarded(() => entry.run(call))),
      ),
    );
  }

  async postToolUse(call: ToolCallFacts, result: ToolResult): Promise<HookDecision> {
    return combine(
      await Promise.all(
        this.#post
          .filter((entry) => matches(entry.matcher, call.toolName))
          .map((entry) => guarded(() => entry.run(call, result))),
      ),
    );
  }

  /** Sequential so publish order across entries is deterministic. */
  async turnBoundary(
    turn: TurnFacts,
    ctx: { notifier: Notifier; runId: AgentRunId },
  ): Promise<void> {
    for (const entry of this.#boundary) {
      try {
        await entry.run(turn, ctx);
      } catch (error) {
        console.warn(
          `turnBoundary hook failed and was skipped: ${errorMessage(error)}`,
        );
      }
    }
  }
}

function matches(matcher: HookMatcher | undefined, toolName: string): boolean {
  return matcher?.toolName === undefined || matcher.toolName === toolName;
}

async function guarded(
  run: () => HookDecision | Promise<HookDecision>,
): Promise<HookDecision> {
  try {
    return await run();
  } catch (error) {
    return { decision: "deny", reason: errorMessage(error) };
  }
}

function combine(decisions: HookDecision[]): HookDecision {
  const reasons = decisions
    .filter((decision) => decision.decision === "deny")
    .map((decision) => decision.reason);
  return reasons.length > 0
    ? { decision: "deny", reason: reasons.join("; ") }
    : { decision: "passthrough" };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
