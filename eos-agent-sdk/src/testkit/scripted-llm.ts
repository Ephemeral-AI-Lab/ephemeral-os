import {
  toolUseIdFrom,
  type ContentBlock,
  type JsonObject,
  type Message,
  type UserMessage,
} from "../contracts/index.js";
import type {
  LlmClient,
  LlmRequest,
  LlmStreamEvent,
  LlmStreamOptions,
  StopReason,
  UsageSnapshot,
} from "../llm-client/index.js";

/** One scripted provider turn; receives the request and the run's signal. */
export type ScriptedTurn = (
  request: LlmRequest,
  signal: AbortSignal | undefined,
) => AsyncIterable<LlmStreamEvent>;

/** In-process `LlmClient` double: one script per provider call, in order. */
export class ScriptedLlmClient implements LlmClient {
  readonly requests: LlmRequest[] = [];
  readonly #turns: ScriptedTurn[];

  constructor(turns: ScriptedTurn[]) {
    this.#turns = turns;
  }

  streamMessage(
    request: LlmRequest,
    options?: LlmStreamOptions,
  ): AsyncIterable<LlmStreamEvent> {
    const script = this.#turns.at(this.requests.length);
    this.requests.push(request);
    if (!script) {
      throw new Error(`unscripted provider call ${String(this.requests.length)}`);
    }
    return script(request, options?.signal);
  }
}

/** A turn that yields the given events, a microtask apart, then completes. */
export function scriptedTurn(events: LlmStreamEvent[]): ScriptedTurn {
  return async function* () {
    for (const event of events) {
      await Promise.resolve();
      yield event;
    }
  };
}

/** A turn whose events are built from the live request (dynamic ids). */
export function dynamicTurn(
  build: (request: LlmRequest) => LlmStreamEvent[],
): ScriptedTurn {
  return async function* (request) {
    for (const event of build(request)) {
      await Promise.resolve();
      yield event;
    }
  };
}

/** A turn that waits for `release`, then yields its events. */
export function gatedTurn(
  release: Promise<void>,
  events: LlmStreamEvent[],
): ScriptedTurn {
  return async function* () {
    await release;
    for (const event of events) {
      await Promise.resolve();
      yield event;
    }
  };
}

/** A turn that signals `onStart`, then hangs until the run's signal aborts. */
export function hangingTurn(onStart?: () => void): ScriptedTurn {
  // eslint-disable-next-line require-yield -- the stream dies before yielding
  return async function* (_request, signal) {
    onStart?.();
    await new Promise<never>((_resolve, reject) => {
      const fail = (): void => {
        reject(new Error("aborted"));
      };
      if (!signal) return;
      if (signal.aborted) fail();
      else signal.addEventListener("abort", fail, { once: true });
    });
  };
}

const USAGE: UsageSnapshot = { input_tokens: 1, output_tokens: 1 };

/** The terminal stream event completing one assistant turn. */
export function complete(
  message: Message,
  stop_reason: StopReason = "end_turn",
): LlmStreamEvent {
  return { type: "assistant_message_complete", message, usage: USAGE, stop_reason };
}

export function assistantMessage(...content: ContentBlock[]): Message {
  return { role: "assistant", content };
}

export function textBlock(text: string): ContentBlock {
  return { type: "text", text };
}

export function toolUseBlock(
  id: string,
  name: string,
  input: JsonObject = {},
): Extract<ContentBlock, { type: "tool_use" }> {
  return { type: "tool_use", tool_use_id: toolUseIdFrom(id), name, input };
}

/** A run-seed / steer entry from raw text. */
export function userMessage(text: string): UserMessage {
  return { role: "user", content: [{ type: "text", text }] };
}

/** The last `tool_result` block in the request, most recent first. */
export function lastToolResult(
  request: LlmRequest,
): Extract<ContentBlock, { type: "tool_result" }> {
  for (const message of [...request.messages].reverse()) {
    for (const block of [...message.content].reverse()) {
      if (block.type === "tool_result") return block;
    }
  }
  throw new Error("no tool_result block in the request");
}
