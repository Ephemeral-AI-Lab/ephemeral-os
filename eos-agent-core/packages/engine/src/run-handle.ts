import type { Message } from "@eos/contracts";
import type { StopReason, UsageSnapshot } from "@eos/llm-client";

import type { DisplayedMessage } from "./conversation.js";
import { EventStream, type AgentEvent } from "./events.js";

/** Why a run failed, typed so callers never parse prose. */
export interface AgentRunFailure {
  /** `max_turns` is restartable with a fresh budget. */
  kind: "provider_error" | "max_turns" | "internal";
  message: string;
}

/** The status arm of `AgentRunOutcome`; assembled at each loop exit. */
export type AgentRunStatus =
  | { status: "completed"; final_message: Message; stop_reason?: StopReason }
  | { status: "cancelled"; reason: string }
  | { status: "failed"; failure: AgentRunFailure };

/**
 * The terminal state of one run. `llm` is provider-valid restart input on
 * every status: each `tool_use` is answered (synthetic results on cancel)
 * and salvaged partials never land in it.
 */
export type AgentRunOutcome = {
  displayed: DisplayedMessage[];
  llm: Message[];
  /** Summed across completed turns. */
  usage: UsageSnapshot;
  turns: number;
} & AgentRunStatus;

/** The public surface of one running agent loop. */
export interface AgentRunHandle {
  /** Single-consumer event stream; `run_finished` is always last. */
  events: AsyncIterable<AgentEvent>;
  /** Resolves after `run_finished` is enqueued; never rejects. */
  outcome: Promise<AgentRunOutcome>;
  /**
   * Queue a user message for the next turn boundary; throws `TypeError` on
   * a non-user role and returns false once finishing has begun.
   */
  steer(message: Message): boolean;
  /**
   * The one stop semantic: abort the run's signal. Idempotent, no-op after
   * finish. `reason` is a label recorded on the cancelled outcome, never a
   * behavior branch.
   */
  interrupt(reason?: string): void;
}

/**
 * Run-handle internals shared with the loop: one event stream, one abort
 * signal (optionally child of a caller scope), one steer queue drained at
 * turn boundaries, and one atomic finish.
 */
export class RunHandle implements AgentRunHandle {
  readonly signal: AbortSignal;
  readonly outcome: Promise<AgentRunOutcome>;
  readonly #stream = new EventStream();
  readonly #controller = new AbortController();
  #steers: Message[] = [];
  #finished = false;
  #cancelReason: string | undefined;
  #resolveOutcome!: (outcome: AgentRunOutcome) => void;

  constructor(parent?: AbortSignal) {
    this.signal = parent
      ? AbortSignal.any([this.#controller.signal, parent])
      : this.#controller.signal;
    this.outcome = new Promise((resolve) => {
      this.#resolveOutcome = resolve;
    });
  }

  get events(): AsyncIterable<AgentEvent> {
    return this.#stream;
  }

  get finished(): boolean {
    return this.#finished;
  }

  /** The recorded interrupt label; external aborts record none. */
  get cancelReason(): string {
    return this.#cancelReason ?? "interrupted";
  }

  steer(message: Message): boolean {
    if (message.role !== "user") {
      throw new TypeError("steer() requires a user message");
    }
    if (this.#finished) return false;
    this.#steers.push(message);
    return true;
  }

  interrupt(reason?: string): void {
    if (this.#finished) return;
    this.#cancelReason ??= reason ?? "interrupted";
    this.#controller.abort();
  }

  /** Loop step 3: take every queued steer, in arrival order. */
  drainSteers(): Message[] {
    const drained = this.#steers;
    this.#steers = [];
    return drained;
  }

  hasPendingSteers(): boolean {
    return this.#steers.length > 0;
  }

  readonly emit = (event: AgentEvent): void => {
    this.#stream.push(event);
  };

  /**
   * The atomic finish: flips `steer()` to false, emits `run_finished`,
   * closes the stream, and resolves `outcome` — exactly once, in one
   * synchronous block.
   */
  finish(outcome: AgentRunOutcome): void {
    if (this.#finished) return;
    this.#finished = true;
    this.#steers = [];
    this.#stream.push({ type: "run_finished", outcome });
    this.#stream.close();
    this.#resolveOutcome(outcome);
  }
}
