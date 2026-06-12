import { toolUseIdFrom } from "../../src/contracts/index.js";

import { createLlmClient } from "../../src/llm-client/factory.js";
import type { ProviderConnection } from "../../src/llm-client/profiles.js";
import { buildLlmRequest, type LlmRequestInit } from "../../src/llm-client/types.js";
import {
  describeLlmClientContract,
  type Scenario,
} from "./contract/llm-client-contract.js";
import {
  errorResponse,
  fetchStub,
  fixture,
  hangingSseResponse,
  sseResponse,
} from "./support.js";

const NO_RETRY = { max_retries: 0, base_delay_s: 0, max_delay_s: 0 };

/** Bind a profile to one canned response; each scenario gets a fresh stub. */
function replay(
  connection: ProviderConnection,
  factory: (init: RequestInit | undefined) => Response,
  init: LlmRequestInit,
): Scenario {
  const stub = fetchStub([factory]);
  return {
    client: createLlmClient(connection, { retry: NO_RETRY, fetch: stub.fetch }),
    request: buildLlmRequest(init),
  };
}

const READ_FILE_TOOL = {
  name: "read_file",
  description: "Read a file",
  input_schema: { type: "object" },
};

/** A tool_use + tool_result exchange ready for replay. */
function roundTripMessages(callId: string): LlmRequestInit["messages"] {
  return [
    { role: "user", content: [{ type: "text", text: "read foo.txt" }] },
    {
      role: "assistant",
      content: [
        {
          type: "tool_use",
          tool_use_id: toolUseIdFrom(callId),
          name: "read_file",
          input: { path: "foo.txt" },
        },
      ],
    },
    {
      role: "user",
      content: [
        {
          type: "tool_result",
          tool_use_id: toolUseIdFrom(callId),
          content: "file body",
          is_error: false,
        },
      ],
    },
  ];
}

const ANTHROPIC_TEXT_SSE = [
  "event: message_start",
  'data: {"type":"message_start","message":{"role":"assistant","usage":{"input_tokens":10,"output_tokens":1}}}',
  "",
  "event: content_block_start",
  'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
  "",
  "event: content_block_delta",
  'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}',
  "",
  "event: content_block_delta",
  'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}',
  "",
  "event: content_block_stop",
  'data: {"type":"content_block_stop","index":0}',
  "",
  "event: message_delta",
  'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":15}}',
  "",
  "event: message_stop",
  'data: {"type":"message_stop"}',
  "",
].join("\n");

const ANTHROPIC_HANGING_PREFIX = [
  "event: content_block_start",
  'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
  "",
  "event: content_block_delta",
  'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"partial"}}',
  "",
  "",
].join("\n");

describeLlmClientContract({
  name: "anthropic messages wire, fixture fetch",
  scenarios: {
    text: () =>
      replay(
        { provider: "anthropic_api", api_key: "test-key" },
        () => sseResponse(ANTHROPIC_TEXT_SSE),
        { model: "claude-test" },
      ),
    toolCall: () =>
      replay(
        { provider: "anthropic_api", api_key: "test-key" },
        () => sseResponse(fixture("./fixtures/anthropic/full.sse")),
        {
          model: "claude-test",
          tools: [READ_FILE_TOOL],
          tool_choice: "any",
        },
      ),
    toolRoundTrip: () =>
      replay(
        { provider: "anthropic_api", api_key: "test-key" },
        () => sseResponse(ANTHROPIC_TEXT_SSE),
        { model: "claude-test", messages: roundTripMessages("toolu_9") },
      ),
    reasoning: () =>
      replay(
        { provider: "anthropic_api", api_key: "test-key" },
        () => sseResponse(fixture("./fixtures/anthropic/full.sse")),
        { model: "claude-test", reasoning_effort: "low" },
      ),
    abort: () =>
      replay(
        { provider: "anthropic_api", api_key: "test-key" },
        (init) => hangingSseResponse(ANTHROPIC_HANGING_PREFIX, init),
        { model: "claude-test" },
      ),
    authFailure: () =>
      replay(
        { provider: "anthropic_api", api_key: "bad-key" },
        () => errorResponse(401),
        { model: "claude-test" },
      ),
  },
  exact: {
    text: "Hello world",
    reasoning: "Let me think",
    toolInput: { path: "foo.txt" },
    usage: { input_tokens: 10, output_tokens: 15 },
  },
});

const OPENAI_TEXT_SSE = [
  'data: {"type":"response.output_text.delta","delta":"Hi"}',
  "",
  'data: {"type":"response.completed","response":{"id":"r","status":"completed","usage":{"input_tokens":5,"output_tokens":4}}}',
  "",
].join("\n");

const OPENAI_REASONING_SSE = [
  'data: {"type":"response.reasoning_summary_text.delta","item_id":"rs_1","delta":"thinking "}',
  "",
  'data: {"type":"response.reasoning_summary_text.delta","item_id":"rs_1","delta":"hard"}',
  "",
  'data: {"type":"response.output_text.delta","delta":"Hi"}',
  "",
  'data: {"type":"response.completed","response":{"id":"r","status":"completed","usage":{"input_tokens":5,"output_tokens":4}}}',
  "",
].join("\n");

const OPENAI_HANGING_PREFIX =
  'data: {"type":"response.output_text.delta","delta":"partial"}\n\n';

describeLlmClientContract({
  name: "openai responses wire, fixture fetch",
  scenarios: {
    text: () =>
      replay(
        { provider: "openai_api", api_key: "test-key" },
        () => sseResponse(OPENAI_TEXT_SSE),
        { model: "gpt-test" },
      ),
    toolCall: () =>
      replay(
        { provider: "openai_api", api_key: "test-key" },
        () => sseResponse(fixture("./fixtures/openai/full.sse")),
        { model: "gpt-test", tools: [READ_FILE_TOOL], tool_choice: "any" },
      ),
    toolRoundTrip: () =>
      replay(
        { provider: "openai_api", api_key: "test-key" },
        () => sseResponse(OPENAI_TEXT_SSE),
        { model: "gpt-test", messages: roundTripMessages("call_1") },
      ),
    reasoning: () =>
      replay(
        { provider: "openai_api", api_key: "test-key" },
        () => sseResponse(OPENAI_REASONING_SSE),
        { model: "gpt-test", reasoning_effort: "low" },
      ),
    abort: () =>
      replay(
        { provider: "openai_api", api_key: "test-key" },
        (init) => hangingSseResponse(OPENAI_HANGING_PREFIX, init),
        { model: "gpt-test" },
      ),
    authFailure: () =>
      replay(
        { provider: "openai_api", api_key: "bad-key" },
        () => errorResponse(401),
        { model: "gpt-test" },
      ),
  },
  exact: {
    text: "Hi",
    reasoning: "thinking hard",
    toolInput: { path: "foo.txt" },
    usage: { input_tokens: 5, output_tokens: 4 },
  },
});
