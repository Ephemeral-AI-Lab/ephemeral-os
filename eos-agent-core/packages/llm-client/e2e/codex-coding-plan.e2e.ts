import { describe } from "vitest";

import { toolUseIdFrom, type Message, type ToolSpec } from "@eos/contracts";

import {
  buildLlmRequest,
  createLlmClient,
  SecretString,
  type LlmClient,
} from "../src/index.js";
import { describeLlmClientContract } from "../tests/contract/llm-client-contract.js";
import { loadCodexAuth } from "./support/codex-auth.js";

const auth = loadCodexAuth();
/** Defaults to the local Codex CLI's configured model at implementation time. */
const MODEL = process.env.CODEX_E2E_MODEL ?? "gpt-5.5";
/** The codex backend requires `instructions`; every scenario sends one. */
const SYSTEM_PROMPT = "You are a terse test assistant.";

if (!auth.available) {
  console.warn(`codex e2e skipped: ${auth.reason}`);
}

function token(): SecretString {
  if (!auth.available) {
    throw new Error("unreachable: the suite is skipped without credentials");
  }
  return auth.accessToken;
}

/** The real token with its signature destroyed: claims parse, the edge 401s. */
function corruptedToken(): SecretString {
  const [header, payload] = token().expose().split(".");
  return new SecretString(`${header}.${payload}.invalidsignature`);
}

function liveClient(accessToken: SecretString): LlmClient {
  return createLlmClient({
    provider: "codex_coding_plan",
    access_token: accessToken,
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
describe.skipIf(!auth.available)("codex coding plan (live)", () => {
  describeLlmClientContract({
    name: "codex_coding_plan live",
    scenarios: {
      text: () => ({
        client: liveClient(token()),
        request: buildLlmRequest({
          model: MODEL,
          system_prompt: SYSTEM_PROMPT,
          messages: [user("Reply with exactly one word: pong")],
        }),
      }),
      toolCall: () => ({
        client: liveClient(token()),
        request: buildLlmRequest({
          model: MODEL,
          system_prompt: SYSTEM_PROMPT,
          tools: [ECHO_TOOL],
          tool_choice: "any",
          messages: [
            user('Call the echo tool exactly once with the text "hello".'),
          ],
        }),
      }),
      toolRoundTrip: () => ({
        client: liveClient(token()),
        request: buildLlmRequest({
          model: MODEL,
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
        client: liveClient(token()),
        request: buildLlmRequest({
          model: MODEL,
          system_prompt: SYSTEM_PROMPT,
          reasoning_effort: "low",
          messages: [user("What is 17 + 25? Reply with just the number.")],
        }),
      }),
      abort: () => ({
        client: liveClient(token()),
        request: buildLlmRequest({
          model: MODEL,
          system_prompt: SYSTEM_PROMPT,
          messages: [user("Count from 1 to 200, one number per line.")],
        }),
      }),
      authFailure: () => ({
        client: liveClient(corruptedToken()),
        request: buildLlmRequest({
          model: MODEL,
          system_prompt: SYSTEM_PROMPT,
          messages: [user("Reply with exactly one word: pong")],
        }),
      }),
    },
  });
});
