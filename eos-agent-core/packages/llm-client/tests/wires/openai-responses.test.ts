import { describe, expect, it } from "vitest";

import { toolUseIdFrom } from "@eos/contracts";

import type { LlmClient } from "../../src/client.js";
import type { ProviderClientOptions } from "../../src/config.js";
import { ProviderError } from "../../src/errors.js";
import { createLlmClient } from "../../src/factory.js";
import { SecretString } from "../../src/secret.js";
import { LlmStreamClient } from "../../src/stream-client.js";
import {
  encodeOpenAiRequest,
  openAiResponsesWire,
} from "../../src/wires/openai-responses.js";
import { buildLlmRequest, type ReasoningEffort } from "../../src/types.js";
import {
  collect,
  collectUntilError,
  errorResponse,
  fetchStub,
  hangingSseResponse,
  sseResponse,
} from "../support.js";

const NO_RETRY = { max_retries: 0, base_delay_s: 0, max_delay_s: 0 };

function client(
  stub: ReturnType<typeof fetchStub>,
  options: ProviderClientOptions = {},
): LlmClient {
  return createLlmClient(
    { provider: "openai_api", api_key: "test-key" },
    { retry: NO_RETRY, fetch: stub.fetch, ...options },
  );
}

function completedSse(response: Record<string, unknown>): string {
  return `data: ${JSON.stringify({ type: "response.completed", response })}\n\n`;
}

describe("openai responses decode (real sdk parser via injected fetch)", () => {
  it("maps reasoning summary deltas to reasoning", async () => {
    const sse = [
      'data: {"type":"response.reasoning_summary_text.delta","item_id":"rs_1","delta":"thinking "}',
      "",
      'data: {"type":"response.reasoning_summary_text.delta","item_id":"rs_1","delta":"hard"}',
      "",
      completedSse({ id: "r", status: "completed", usage: { input_tokens: 1, output_tokens: 2 } }),
    ].join("\n");
    const stub = fetchStub([() => sseResponse(sse)]);
    const events = await collect(
      client(stub).streamMessage(buildLlmRequest({ model: "gpt" })),
    );
    expect(events[0]).toEqual({ type: "reasoning_delta", text: "thinking " });
    expect(events[1]).toEqual({ type: "reasoning_delta", text: "hard" });
    const complete = events.at(-1);
    if (complete?.type !== "assistant_message_complete") {
      throw new Error("expected a completion event");
    }
    expect(complete.message.content).toEqual([
      { type: "reasoning", text: "thinking hard" },
    ]);
  });

  it("derives stop reasons per the §5 table", async () => {
    const run = async (sse: string) => {
      const stub = fetchStub([() => sseResponse(sse)]);
      const events = await collect(
        client(stub).streamMessage(buildLlmRequest({ model: "gpt" })),
      );
      const complete = events.at(-1);
      if (complete?.type !== "assistant_message_complete") {
        throw new Error("expected a completion event");
      }
      return complete;
    };

    // complete, no calls -> end_turn
    const endTurn = await run(
      completedSse({ id: "r", status: "completed", usage: { input_tokens: 1, output_tokens: 1 } }),
    );
    expect(endTurn.stop_reason).toBe("end_turn");
    expect(endTurn.message.content).toEqual([]);

    // incomplete on max_output_tokens -> max_tokens
    const maxTokens = await run(
      `data: ${JSON.stringify({
        type: "response.incomplete",
        response: {
          id: "r",
          status: "incomplete",
          incomplete_details: { reason: "max_output_tokens" },
          usage: { input_tokens: 1, output_tokens: 1 },
        },
      })}\n\n`,
    );
    expect(maxTokens.stop_reason).toBe("max_tokens");

    // any other incomplete reason passes through verbatim
    const filtered = await run(
      `data: ${JSON.stringify({
        type: "response.incomplete",
        response: {
          id: "r",
          status: "incomplete",
          incomplete_details: { reason: "content_filter" },
        },
      })}\n\n`,
    );
    expect(filtered.stop_reason).toBe("content_filter");
  });

  it("normalizes cached input tokens out of input_tokens", async () => {
    const stub = fetchStub([
      () =>
        sseResponse(
          completedSse({
            id: "r",
            status: "completed",
            usage: {
              input_tokens: 50,
              output_tokens: 7,
              input_tokens_details: { cached_tokens: 30 },
            },
          }),
        ),
    ]);
    const events = await collect(
      client(stub).streamMessage(buildLlmRequest({ model: "gpt" })),
    );
    const complete = events.at(-1);
    if (complete?.type !== "assistant_message_complete") {
      throw new Error("expected a completion event");
    }
    expect(complete.usage).toEqual({
      input_tokens: 20,
      output_tokens: 7,
      cache_read_input_tokens: 30,
    });
  });

  it("treats a stream without a terminal response event as truncated", async () => {
    const sse =
      'data: {"type":"response.output_text.delta","delta":"partial"}\n\n';
    const stub = fetchStub([
      () => sseResponse(sse, { "x-request-id": "req-trunc" }),
    ]);
    const { events, error } = await collectUntilError(
      client(stub).streamMessage(buildLlmRequest({ model: "gpt" })),
    );
    expect(events).toEqual([
      { type: "assistant_text_delta", text: "partial" },
    ]);
    const provider = error as ProviderError;
    expect(provider.kind).toBe("decode");
    expect(provider.truncated).toBe(true);
    expect(provider.request_id).toBe("req-trunc");
  });

  it("surfaces response.failed as a decode error with the provider message", async () => {
    const sse = `data: ${JSON.stringify({
      type: "response.failed",
      response: {
        id: "r",
        status: "failed",
        error: { code: "server_error", message: "model exploded" },
      },
    })}\n\n`;
    const stub = fetchStub([() => sseResponse(sse)]);
    const { error } = await collectUntilError(
      client(stub).streamMessage(buildLlmRequest({ model: "gpt" })),
    );
    const provider = error as ProviderError;
    expect(provider.kind).toBe("decode");
    expect(provider.message).toContain("model exploded");
  });
});

describe("openai responses transport reliability", () => {
  it("aborts an idle stream as a transport failure", async () => {
    const stub = fetchStub([
      (init) =>
        hangingSseResponse(
          'data: {"type":"response.created","response":{"id":"r","status":"in_progress"}}\n\n',
          init,
        ),
    ]);
    const idleClient = client(stub, {
      retry: NO_RETRY,
      streamGuard: { idle_timeout_s: 0.05 },
      fetch: stub.fetch,
    });
    const { error } = await collectUntilError(
      idleClient.streamMessage(buildLlmRequest({ model: "gpt" })),
    );
    expect(error).toBeInstanceOf(ProviderError);
    expect((error as ProviderError).kind).toBe("transport");
  });

  it("rethrows the abort error as-is when the caller cancels mid-stream", async () => {
    const stub = fetchStub([
      (init) =>
        hangingSseResponse(
          'data: {"type":"response.output_text.delta","delta":"partial"}\n\n',
          init,
        ),
    ]);
    const controller = new AbortController();
    const pending = collectUntilError(
      client(stub).streamMessage(buildLlmRequest({ model: "gpt" }), {
        signal: controller.signal,
      }),
    );
    setTimeout(() => { controller.abort(); }, 20);
    const { error } = await pending;
    expect(controller.signal.aborted).toBe(true);
    // Classified by signal.aborted, never by error type.
    expect(error).toBe(controller.signal.reason);
  });

  it("passes transport headers() output on each attempt", async () => {
    const stub = fetchStub([
      () => errorResponse(503),
      () =>
        sseResponse(
          completedSse({ id: "r", status: "completed", usage: { input_tokens: 0, output_tokens: 0 } }),
        ),
    ]);
    let attempts = 0;
    const wire = openAiResponsesWire({
      baseUrl: "https://api.openai.com/v1",
      credential: { kind: "bearer", secret: new SecretString("k") },
      headers: () => {
        attempts += 1;
        return Promise.resolve({ "x-attempt": String(attempts) });
      },
      fetch: stub.fetch,
    });
    const headerClient = new LlmStreamClient(wire, {}, {
      retry: { ...NO_RETRY, max_retries: 1 },
    });
    await collect(headerClient.streamMessage(buildLlmRequest({ model: "gpt" })));
    expect(stub.calls, "one fetch per attempt").toHaveLength(2);
    expect(
      new Headers(stub.calls[0].init?.headers).get("x-attempt"),
      "first attempt headers",
    ).toBe("1");
    expect(
      new Headers(stub.calls[1].init?.headers).get("x-attempt"),
      "second attempt headers",
    ).toBe("2");
  });
});

describe("openai responses encode projection (§5 column)", () => {
  it("projects the full request surface", () => {
    const params = encodeOpenAiRequest(
      buildLlmRequest({
        model: "gpt-test",
        system_prompt: "be terse",
        max_tokens: 256,
        messages: [
          { role: "user", content: [{ type: "text", text: "hi" }] },
          {
            role: "assistant",
            content: [
              { type: "reasoning", text: "private" },
              { type: "text", text: "hello" },
              {
                type: "tool_use",
                tool_use_id: toolUseIdFrom("call_1"),
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
                tool_use_id: toolUseIdFrom("call_1"),
                content: "file body",
                is_error: false,
              },
            ],
          },
        ],
        tools: [
          {
            name: "read_file",
            description: "Read a file",
            input_schema: { type: "object" },
            output_schema: { type: "string" },
          },
        ],
        tool_choice: { tool: "read_file" },
        reasoning_effort: "max",
      }),
    );

    expect(params.stream).toBe(true);
    expect(params.store).toBe(false);
    expect(params.instructions).toBe("be terse");
    expect(params.max_output_tokens).toBe(256);
    expect(params.input).toEqual([
      {
        type: "message",
        role: "user",
        content: [{ type: "input_text", text: "hi" }],
      },
      {
        // Reasoning blocks are dropped on encode (provider-managed).
        type: "message",
        role: "assistant",
        content: [{ type: "output_text", text: "hello" }],
      },
      {
        type: "function_call",
        call_id: "call_1",
        name: "read_file",
        arguments: '{"path":"foo.txt"}',
      },
      {
        type: "function_call_output",
        call_id: "call_1",
        output: "file body",
      },
    ]);
    // output_schema is mapped for openai; schemas are not strict-mode shaped.
    expect(params.tools).toEqual([
      {
        type: "function",
        name: "read_file",
        description: "Read a file",
        parameters: { type: "object" },
        strict: false,
        output_schema: { type: "string" },
      },
    ]);
    expect(params.tool_choice).toEqual({ type: "function", name: "read_file" });
    // max clamps to high.
    expect(params.reasoning).toEqual({ effort: "high" });
  });

  it("maps tool_choice auto and any", () => {
    expect(
      encodeOpenAiRequest(buildLlmRequest({ model: "m", tool_choice: "auto" }))
        .tool_choice,
    ).toBe("auto");
    expect(
      encodeOpenAiRequest(buildLlmRequest({ model: "m", tool_choice: "any" }))
        .tool_choice,
    ).toBe("required");
  });

  const effortClamps: [ReasoningEffort, string][] = [
    ["minimal", "minimal"],
    ["low", "low"],
    ["medium", "medium"],
    ["high", "high"],
    ["max", "high"],
  ];

  it.each(effortClamps)("clamps effort %s to %s per the §5 table", (effort, clamped) => {
    expect(
      encodeOpenAiRequest(
        buildLlmRequest({ model: "m", reasoning_effort: effort }),
      ).reasoning?.effort,
    ).toBe(clamped);
  });

  it("encodes the codex dialect: no completion cap, forced tool clamps to required (§4.1)", () => {
    const request = buildLlmRequest({
      model: "gpt-test",
      max_tokens: 256,
      tools: [
        {
          name: "read_file",
          description: "Read a file",
          input_schema: { type: "object" },
        },
      ],
      tool_choice: { tool: "read_file" },
    });

    const codex = encodeOpenAiRequest(request, { dialect: "codex" });
    expect(codex, "codex omits the completion cap").not.toHaveProperty(
      "max_output_tokens",
    );
    expect(codex.tool_choice, "forced tool clamps to required").toBe(
      "required",
    );
    expect(codex.store, "stateless replay holds across dialects").toBe(false);

    const publicDialect = encodeOpenAiRequest(request, { dialect: "public" });
    expect(publicDialect.max_output_tokens).toBe(256);
    expect(publicDialect.tool_choice).toEqual({
      type: "function",
      name: "read_file",
    });
  });

  it("sends bearer credentials to the responses endpoint", async () => {
    const stub = fetchStub([
      () =>
        sseResponse(
          completedSse({ id: "r", status: "completed", usage: { input_tokens: 0, output_tokens: 0 } }),
        ),
    ]);
    await collect(
      client(stub).streamMessage(buildLlmRequest({ model: "gpt-test" })),
    );
    expect(stub.calls).toHaveLength(1);
    const call = stub.calls[0];
    expect(call.url).toBe("https://api.openai.com/v1/responses");
    const headers = new Headers(call.init?.headers);
    expect(headers.get("authorization")).toBe("Bearer test-key");
    const body = call.body as Record<string, unknown>;
    expect(body.model).toBe("gpt-test");
    expect(body.stream).toBe(true);
    expect(body.store).toBe(false);
    expect(body).not.toHaveProperty("instructions");
    expect(body).not.toHaveProperty("tools");
    expect(body).not.toHaveProperty("reasoning");
  });
});
