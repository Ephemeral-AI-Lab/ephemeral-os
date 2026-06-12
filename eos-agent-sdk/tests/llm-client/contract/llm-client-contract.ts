import { describe, expect, it } from "vitest";

import {
  assistantText,
  reasoningText,
  toolUses,
  type JsonObject,
} from "../../../src/contracts/index.js";

import type { LlmClient } from "../../../src/llm-client/client.js";
import { ProviderError } from "../../../src/llm-client/errors.js";
import type { LlmStreamEvent } from "../../../src/llm-client/events.js";
import type { LlmRequest, UsageSnapshot } from "../../../src/llm-client/types.js";
import { collect, collectUntilError } from "../support.js";

export interface Scenario {
  client: LlmClient;
  request: LlmRequest;
}

/**
 * One binding of the `LlmClient` leg contract (Phase 02 §4.5): the
 * scenarios decide how each behavior is induced (fixture-fetch client vs
 * live profile + eliciting prompt); the assertions are shared. Strictness is
 * data: deterministic bindings add the `exact` golden block, live bindings
 * omit it. There is no live/unit branching inside scenario bodies.
 */
export interface LlmClientContractBinding {
  name: string;
  scenarios: {
    /** A plain text turn: no tool use, ends `end_turn`. */
    text: () => Scenario;
    /** Offers one tool (live: `echo`) and elicits a call. */
    toolCall: () => Scenario;
    /** History replaying a tool_use + tool_result pair; ends as text. */
    toolRoundTrip?: () => Scenario;
    /** A turn eliciting reasoning (live: `reasoning_effort: "low"`). */
    reasoning?: () => Scenario;
    /** A stream long enough to abort after its first event. */
    abort?: () => Scenario;
    /** A connection whose credential the endpoint rejects with 401. */
    authFailure?: () => Scenario;
  };
  /** Golden pinning for deterministic bindings; omitted by live bindings. */
  exact?: {
    text?: string;
    reasoning?: string;
    toolInput?: JsonObject;
    usage?: UsageSnapshot;
  };
}

type CompletionEvent = Extract<
  LlmStreamEvent,
  { type: "assistant_message_complete" }
>;

/** Delta grammar: exactly one completion, in final position. */
function completionOf(events: LlmStreamEvent[]): CompletionEvent {
  const completions = events.filter(
    (event): event is CompletionEvent =>
      event.type === "assistant_message_complete",
  );
  expect(completions, "exactly one assistant_message_complete").toHaveLength(1);
  expect(
    events.at(-1)?.type,
    "the completion is the final event",
  ).toBe("assistant_message_complete");
  return completions[0];
}

export function describeLlmClientContract(
  binding: LlmClientContractBinding,
): void {
  const { scenarios, exact } = binding;

  describe(`llm client contract (${binding.name})`, () => {
    it("streams text deltas, then exactly one completion, then ends", async () => {
      const { client, request } = scenarios.text();
      const stream = client.streamMessage(request);
      const events = await collect(stream);
      const textDeltas = events.flatMap((event) =>
        event.type === "assistant_text_delta" ? [event.text] : [],
      );
      expect(
        textDeltas.length,
        "at least one text delta",
      ).toBeGreaterThanOrEqual(1);
      const completion = completionOf(events);
      expect(
        completion.usage.input_tokens,
        "input tokens reported",
      ).toBeGreaterThan(0);
      expect(
        completion.usage.output_tokens,
        "output tokens reported",
      ).toBeGreaterThan(0);
      expect(completion.stop_reason).toBe("end_turn");
      expect(
        await collect(stream),
        "single pass: re-leg yields nothing",
      ).toEqual([]);
      if (exact?.text !== undefined) {
        expect(textDeltas.join(""), "delta concatenation").toBe(exact.text);
        expect(
          assistantText(completion.message),
          "assembled message text",
        ).toBe(exact.text);
      }
      if (exact?.usage !== undefined) {
        expect(completion.usage).toEqual(exact.usage);
      }
    });

    it("assembles an offered tool call with a provider-assigned id and object input", async () => {
      const { client, request } = scenarios.toolCall();
      const events = await collect(client.streamMessage(request));
      const toolDeltas = events.flatMap((event) =>
        event.type === "tool_use_delta" ? [event] : [],
      );
      expect(toolDeltas, "exactly one tool_use_delta").toHaveLength(1);
      const delta = toolDeltas[0];
      expect(delta.tool_use_id, "provider-assigned id").not.toBe("");
      expect(delta.name, "tool name").not.toBe("");
      const completion = completionOf(events);
      expect(completion.stop_reason).toBe("tool_use");
      const tools = toolUses(completion.message);
      expect(
        tools,
        "tool block present in the completed message",
      ).toHaveLength(1);
      expect(tools[0].tool_use_id, "delta and message agree on the id").toBe(
        delta.tool_use_id,
      );
      expect(tools[0].input, "delta and message agree on the input").toEqual(
        delta.input,
      );
      if (exact?.toolInput !== undefined) {
        expect(delta.input).toEqual(exact.toolInput);
      }
    });

    const toolRoundTrip = scenarios.toolRoundTrip;
    if (toolRoundTrip) {
      it("accepts a replayed tool round trip and completes as text", async () => {
        const { client, request } = toolRoundTrip();
        const events = await collect(client.streamMessage(request));
        const textDeltas = events.flatMap((event) =>
          event.type === "assistant_text_delta" ? [event.text] : [],
        );
        expect(
          textDeltas.length,
          "a normal text completion after the tool result",
        ).toBeGreaterThanOrEqual(1);
        expect(completionOf(events).stop_reason).toBe("end_turn");
      });
    }

    const reasoning = scenarios.reasoning;
    if (reasoning) {
      it("emits zero or more reasoning deltas with a clean terminus", async () => {
        const { client, request } = reasoning();
        const events = await collect(client.streamMessage(request));
        const reasoningDeltas = events.flatMap((event) =>
          event.type === "reasoning_delta" ? [event.text] : [],
        );
        const completion = completionOf(events);
        if (exact?.reasoning !== undefined) {
          expect(reasoningDeltas.join(""), "delta concatenation").toBe(
            exact.reasoning,
          );
          expect(
            reasoningText(completion.message),
            "assembled reasoning",
          ).toBe(exact.reasoning);
        }
      });
    }

    const abort = scenarios.abort;
    if (abort) {
      it("rethrows the caller's abort as-is, classified by signal.aborted", async () => {
        const { client, request } = abort();
        const controller = new AbortController();
        const events: LlmStreamEvent[] = [];
        let caught: unknown;
        let completed = false;
        try {
          for await (const event of client.streamMessage(request, {
            signal: controller.signal,
          })) {
            events.push(event);
            controller.abort();
          }
          completed = true;
        } catch (error) {
          caught = error;
        }
        expect(completed, "the aborted stream must throw").toBe(false);
        expect(
          events.length,
          "abort fired after the first event",
        ).toBeGreaterThanOrEqual(1);
        expect(controller.signal.aborted).toBe(true);
        // Classified by signal.aborted, never by error type.
        expect(caught, "abort error rethrown as-is").toBe(
          controller.signal.reason,
        );
      });
    }

    const authFailure = scenarios.authFailure;
    if (authFailure) {
      it("maps a rejected credential to an authentication error with status 401", async () => {
        const { client, request } = authFailure();
        const { error } = await collectUntilError(
          client.streamMessage(request),
        );
        expect(error).toBeInstanceOf(ProviderError);
        const provider = error as ProviderError;
        expect(provider.kind).toBe("authentication");
        expect(provider.status_code).toBe(401);
      });
    }
  });
}
