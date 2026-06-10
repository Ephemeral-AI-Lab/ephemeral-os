import type { JsonObject, ToolUseId } from "@eos/contracts";
import type { LlmStreamEvent } from "@eos/llm-client";

import type { AgentRunOutcome } from "./run-handle.js";

/**
 * The engine's `type`-discriminated event union: the provider stream events
 * forwarded unchanged, plus tool execution and run lifecycle. Payload fields
 * are snake_case because this union will cross an SSE/WebSocket boundary in
 * a later phase. `run_finished` is always the final event of a run.
 */
export type AgentEvent =
  | {
      /** A provider call is about to start; 1-based, after the steer drain. */
      type: "turn_started";
      turn: number;
    }
  | LlmStreamEvent
  | {
      /** A tool call left the queue and began executing. */
      type: "tool_execution_started";
      tool_use_id: ToolUseId;
      name: string;
      input: JsonObject;
    }
  | {
      /** A tool call settled (result, mapped error, or unknown tool). */
      type: "tool_execution_completed";
      tool_use_id: ToolUseId;
      name: string;
      output: string;
      is_error: boolean;
    }
  | {
      /** Terminal event; the iterable completes after it. */
      type: "run_finished";
      outcome: AgentRunOutcome;
    };

/**
 * A push-fed queue consumed as one pull-based `AsyncIterable`:
 *
 * - single consumer: a second `[Symbol.asyncIterator]()` call throws,
 * - pushes never block the loop; the buffer is unbounded (the consumer is
 *   in-process; backpressure belongs to the server phase),
 * - `close()` completes iteration once the buffer drains,
 * - an early `break`/`return()` detaches: later pushes are discarded while
 *   the run continues; a stream nobody iterates retains every event.
 */
export class EventStream implements AsyncIterable<AgentEvent> {
  #buffer: AgentEvent[] = [];
  #wakers: (() => void)[] = [];
  #closed = false;
  #detached = false;
  #consumed = false;

  push(event: AgentEvent): void {
    if (this.#detached) return;
    this.#buffer.push(event);
    this.#wake();
  }

  close(): void {
    this.#closed = true;
    this.#wake();
  }

  [Symbol.asyncIterator](): AsyncIterator<AgentEvent, undefined> {
    if (this.#consumed) {
      throw new Error("EventStream supports a single consumer");
    }
    this.#consumed = true;
    return {
      next: () => this.#next(),
      return: () => {
        this.#detach();
        return Promise.resolve<IteratorResult<AgentEvent, undefined>>({
          done: true,
          value: undefined,
        });
      },
    };
  }

  async #next(): Promise<IteratorResult<AgentEvent, undefined>> {
    for (;;) {
      if (this.#detached) return { done: true, value: undefined };
      const event = this.#buffer.shift();
      if (event) return { done: false, value: event };
      if (this.#closed) return { done: true, value: undefined };
      await new Promise<void>((resolve) => {
        this.#wakers.push(resolve);
      });
    }
  }

  #detach(): void {
    this.#detached = true;
    this.#buffer.length = 0;
    this.#wake();
  }

  #wake(): void {
    const wakers = this.#wakers;
    this.#wakers = [];
    for (const waker of wakers) waker();
  }
}
