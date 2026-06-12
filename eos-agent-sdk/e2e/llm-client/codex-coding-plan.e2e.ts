import { describe, expect, it } from "vitest";

import { toolUseIdFrom, type Message, type ToolSpec } from "../../src/contracts/index.js";

import {
  buildLlmRequest,
  type LlmClient,
  type LlmRequestInit,
} from "../../src/llm-client/index.js";
import { describeLlmClientContract } from "../../tests/llm-client/contract/llm-client-contract.js";
import { loadConfiguredCodexClient } from "./support/llm-clients-config.js";

const codex = loadConfiguredCodexClient();
/** The codex backend requires `instructions`; every scenario sends one. */
const SYSTEM_PROMPT = "You are a terse test assistant.";

if (!codex.available) {
  console.warn(`codex e2e skipped: ${codex.reason}`);
}

function config() {
  if (!codex.available) {
    throw new Error("unreachable: the suite is skipped without credentials");
  }
  return codex;
}

function liveClient(): LlmClient {
  return config().createClient();
}

function corruptedClient(): LlmClient {
  return config().createCorruptedClient();
}

function request(init: Omit<LlmRequestInit, "model">) {
  const clientConfig = config();
  return buildLlmRequest({
    model: clientConfig.model,
    reasoning_effort: clientConfig.reasoningEffort,
    ...init,
  });
}

function user(text: string): Message {
  return { role: "user", content: [{ type: "text", text }] };
}

const ECHO_TOOL: ToolSpec = {
  name: "echo",
  description: "Echo the given text back verbatim.",
  input_schema: {
    type: "object",
    properties: { text: { type: "string" } },
    required: ["text"],
  },
};

// Budget guard: at most ~6 small one-word-answer requests per run; every
// assertion is structural (event shapes, kinds, ids), never on model prose.
describe.skipIf(!codex.available)("codex coding plan (live)", () => {
  it("loads codex_coding_plan from llm_clients.json with medium effort", () => {
    const clientConfig = config();
    expect(clientConfig.id).toBe("codex_coding_plan");
    expect(clientConfig.model).toBeTruthy();
    expect(clientConfig.reasoningEffort).toBe("medium");
    expect(liveClient()).toBeTruthy();
  });

  describeLlmClientContract({
    name: "codex_coding_plan live",
    scenarios: {
      text: () => ({
        client: liveClient(),
        request: request({
          system_prompt: SYSTEM_PROMPT,
          messages: [user("Reply with exactly one word: pong")],
        }),
      }),
      toolCall: () => ({
        client: liveClient(),
        request: request({
          system_prompt: SYSTEM_PROMPT,
          tools: [ECHO_TOOL],
          tool_choice: "any",
          messages: [
            user('Call the echo tool exactly once with the text "hello".'),
          ],
        }),
      }),
      toolRoundTrip: () => ({
        client: liveClient(),
        request: request({
          system_prompt: SYSTEM_PROMPT,
          tools: [ECHO_TOOL],
          messages: [
            user('Call the echo tool exactly once with the text "hello".'),
            {
              role: "assistant",
              content: [
                {
                  type: "tool_use",
                  tool_use_id: toolUseIdFrom("call_e2e_roundtrip_1"),
                  name: "echo",
                  input: { text: "hello" },
                },
              ],
            },
            {
              role: "user",
              content: [
                {
                  type: "tool_result",
                  tool_use_id: toolUseIdFrom("call_e2e_roundtrip_1"),
                  content: "hello",
                  is_error: false,
                },
              ],
            },
          ],
        }),
      }),
      reasoning: () => ({
        client: liveClient(),
        request: request({
          system_prompt: SYSTEM_PROMPT,
          messages: [user("What is 17 + 25? Reply with just the number.")],
        }),
      }),
      abort: () => ({
        client: liveClient(),
        request: request({
          system_prompt: SYSTEM_PROMPT,
          messages: [user("Count from 1 to 200, one number per line.")],
        }),
      }),
      authFailure: () => ({
        client: corruptedClient(),
        request: request({
          system_prompt: SYSTEM_PROMPT,
          messages: [user("Reply with exactly one word: pong")],
        }),
      }),
    },
  });
});
