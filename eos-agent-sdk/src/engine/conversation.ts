import type { ContentBlock, Message, UserMessage } from "../contracts/index.js";

/** A `tool_result` content block, the unit `appendToolResults` wraps. */
export type ToolResultBlock = Extract<ContentBlock, { type: "tool_result" }>;

/** Why a salvaged partial assistant message never completed. */
export type PartialReason = "interrupted" | "provider_error";

/** Where one appended user message came from. */
type UserMessageOrigin = "initial" | "steer" | "notification";

/**
 * One line of the `messages.jsonl` conversation artifact, fed to the
 * records sink as it is appended. `assistant_partial` is salvage only —
 * it never reaches `llmMessages()`.
 */
export type ConversationRecord =
  | { kind: "user"; origin: UserMessageOrigin; message: Message }
  | { kind: "assistant"; message: Message }
  | { kind: "assistant_partial"; reason: PartialReason; message: Message }
  | { kind: "tool_results"; message: Message };

/**
 * The single provider-history list plus the records sink. Every append
 * writes both in one call (single-writer rule); `llmMessages()` is the
 * only history source for a provider request, and the read-only snapshot
 * handed to every tool batch.
 */
export class Conversation {
  #llm: Message[] = [];
  readonly #record: ((entry: ConversationRecord) => void) | undefined;

  constructor(
    initial: readonly UserMessage[],
    record?: (entry: ConversationRecord) => void,
  ) {
    this.#record = record;
    for (const message of initial) this.appendUser(message, "initial");
  }

  /** Seed, steered, and drained-notification user input. */
  appendUser(message: UserMessage, origin: UserMessageOrigin): void {
    this.#llm.push(message);
    this.#record?.({ kind: "user", origin, message });
  }

  /** A completed assistant turn. */
  appendAssistant(message: Message): void {
    this.#llm.push(message);
    this.#record?.({ kind: "assistant", message });
  }

  /** One batch's results as a single user message, in `tool_use` order. */
  appendToolResults(blocks: ToolResultBlock[]): void {
    const message: Message = { role: "user", content: blocks };
    this.#llm.push(message);
    this.#record?.({ kind: "tool_results", message });
  }

  /** Salvaged partial assistant output; records-only, never provider history. */
  appendPartialAssistant(partial: Message, reason: PartialReason): void {
    this.#record?.({ kind: "assistant_partial", reason, message: partial });
  }

  /** The ONLY history source for an `LlmRequest`. */
  llmMessages(): readonly Message[] {
    return this.#llm;
  }
}
