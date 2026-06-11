import type {
  AgentRunSnapshot,
  BackgroundSessionSnapshot,
} from "@eos/contracts";
import { executeJsonCommand } from "@eos/scripts";

import { systemNotificationMessage, type NotificationInbox } from "./inbox.js";
import type { LoopObserver, TurnFacts } from "./loop-observer.js";
import {
  TriggerOutputSchema,
  type CommandScript,
  type TriggerCommandRun,
  type TriggerCommandRunner,
  type TriggerPayload,
  type TriggerRuleEntry,
} from "./triggers.js";

/**
 * The execute-backed implementation of the `TriggerCommandRunner` seam,
 * over the shared JSON-command mechanics in `@eos/scripts` (shell
 * execution, payload JSON + newline on stdin, per-command timeout). Never
 * rejects: every failure — execute fault, timeout, nonzero exit, bad
 * JSON, schema mismatch — settles as a `warning` and the firing is
 * dropped.
 */
export const runTriggerCommand: TriggerCommandRunner = async (command, payload) => {
  let settled;
  try {
    settled = await executeJsonCommand(command, payload);
  } catch (error) {
    return {
      warning: `trigger command failed: ${error instanceof Error ? error.message : String(error)}`,
    };
  }
  if (settled.kind === "execute_error") {
    return { warning: `trigger command failed to execute: ${settled.message}` };
  }
  if (settled.kind === "aborted") {
    return { warning: "trigger command timed out" };
  }
  if (settled.code !== 0) {
    return {
      warning: `trigger command exited ${String(settled.code)}: ${settled.stderr.trim() || "(no stderr)"}`,
    };
  }
  const trimmed = settled.stdout.trim();
  if (!trimmed) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return { warning: `trigger stdout was not JSON: ${trimmed.slice(0, 200)}` };
  }
  const checked = TriggerOutputSchema.safeParse(parsed);
  if (!checked.success) {
    return {
      warning: `trigger stdout did not match TriggerOutput: ${checked.error.issues
        .map((issue) => issue.message)
        .join("; ")}`,
    };
  }
  return checked.data.notification === undefined
    ? {}
    : { notification: checked.data.notification };
};

type IdleRule = Extract<TriggerRuleEntry, { event: "IdleParked" }>;

export interface NotificationTriggerEngineDeps {
  /** Already narrowed to this run by the `agent_name`/`agent_kind` matchers. */
  rules: readonly TriggerRuleEntry[];
  /** The execute-backed `runTriggerCommand` in production; stubbed in unit tests. */
  runCommand: TriggerCommandRunner;
  inbox: NotificationInbox;
  /** Background-session list at fire time, not park time. */
  listBackgroundSessions: () => readonly BackgroundSessionSnapshot[];
  runSnapshot: () => AgentRunSnapshot;
  /** `null` when the run's profile terminates on text. */
  terminalTool: string | null;
}

/**
 * The runtime's `LoopObserver` (Phase 04.9): runs operator trigger rules
 * against loop lifecycle facts and publishes their answers as
 * `{type: "reminder"}` notifications into the run's inbox. Notification-only:
 * scripts inform, the model acts. Never throws or rejects — every script
 * failure is logged and the firing dropped, matching the inbox's `onDrained`
 * precedent. Timers live here so the engine stays clock-free; a generation
 * counter makes idle-timer fires atomic per park epoch.
 */
export class NotificationTriggerEngine implements LoopObserver {
  readonly #deps: NotificationTriggerEngineDeps;
  readonly #idleRules: IdleRule[];
  #generation = 0;
  #timers: NodeJS.Timeout[] = [];

  constructor(deps: NotificationTriggerEngineDeps) {
    this.#deps = deps;
    this.#idleRules = deps.rules.filter(
      (rule): rule is IdleRule => rule.event === "IdleParked",
    );
  }

  /**
   * Awaited by the loop: with matching rules, a published reminder is in
   * the inbox before the next provider call; with none, resolves
   * immediately.
   */
  async turnCompleted(facts: TurnFacts): Promise<void> {
    const commands = this.#deps.rules
      .filter((rule) => rule.event === "TurnCompleted")
      .flatMap((rule) => rule.rules);
    if (commands.length === 0) return;
    await this.#run(commands, {
      event: "TurnCompleted",
      facts: {
        turn: facts.turn,
        max_turns: facts.maxTurns,
        tool_calls: facts.toolCalls,
        background_session_count: facts.backgroundSessionCount,
        has_pending_steers: facts.hasPendingSteers,
      },
    });
  }

  /** One shot per park: each `IdleParked` rule arms its own timer. */
  idleStarted(): void {
    this.#generation += 1;
    const generation = this.#generation;
    const since = Date.now();
    for (const rule of this.#idleRules) {
      this.#timers.push(
        setTimeout(() => {
          void this.#fire(rule, generation, since);
        }, rule.timeout_ms),
      );
    }
  }

  /** Any wake (settlement, steer, abort, run finish) disarms the park. */
  idleEnded(): void {
    this.#generation += 1;
    for (const timer of this.#timers) clearTimeout(timer);
    this.#timers = [];
  }

  async #fire(rule: IdleRule, generation: number, since: number): Promise<void> {
    await this.#run(
      rule.rules,
      {
        event: "IdleTimeout",
        facts: { idle_elapsed_ms: Date.now() - since, timeout_ms: rule.timeout_ms },
      },
      // Script execution takes time and a natural wake can land
      // mid-execution: a fire whose park epoch moved is discarded, never
      // published into a later phase of the run.
      () => this.#generation === generation,
    );
  }

  async #run(
    commands: readonly CommandScript[],
    occurrence: Pick<TriggerPayload, "event" | "facts">,
    stillCurrent: () => boolean = () => true,
  ): Promise<void> {
    let runs: TriggerCommandRun[];
    try {
      const payload: TriggerPayload = {
        ...occurrence,
        run: this.#deps.runSnapshot(),
        terminal_tool: this.#deps.terminalTool,
        background_sessions: this.#deps.listBackgroundSessions(),
      };
      runs = await Promise.all(
        commands.map((command) => this.#deps.runCommand(command, payload)),
      );
    } catch (error) {
      // The observer contract: never throws, never rejects. A runner that
      // rejects (the execute-backed one never does) drops the whole firing.
      this.#warn(occurrence.event, error instanceof Error ? error.message : String(error));
      return;
    }
    if (!stillCurrent()) return;
    for (const run of runs) {
      if (run.warning !== undefined) {
        this.#warn(occurrence.event, run.warning);
      } else if (run.notification !== undefined) {
        this.#deps.inbox.publish(
          systemNotificationMessage({
            type: "reminder",
            source: occurrence.event,
            text: run.notification,
          }),
        );
      }
    }
  }

  #warn(event: TriggerPayload["event"], message: string): void {
    console.warn(`notification trigger (${event}): ${message}`);
  }
}
