import { describe, expect, it } from "vitest";

import { ProviderError } from "../../src/llm-client/errors.js";
import { createLlmClient } from "../../src/llm-client/factory.js";
import {
  ProviderConnectionSchema,
  type ProviderConnection,
} from "../../src/llm-client/profiles.js";
import { SecretString } from "../../src/llm-client/secret.js";
import { encodeAnthropicRequest } from "../../src/llm-client/wires/anthropic-messages.js";
import { encodeOpenAiRequest } from "../../src/llm-client/wires/openai-responses.js";
import { buildLlmRequest } from "../../src/llm-client/types.js";
import { collect, fetchStub, fixture, sseResponse } from "./support.js";

const NO_RETRY = { max_retries: 0, base_delay_s: 0, max_delay_s: 0 };

const CLAUDE_CODE_PREFIX =
  "You are Claude Code, Anthropic's official CLI for Claude.";

const ANTHROPIC_STOP = 'event: message_stop\ndata: {"type":"message_stop"}\n\n';
const OPENAI_STOP = `data: ${JSON.stringify({
  type: "response.completed",
  response: { id: "r", status: "completed", usage: { input_tokens: 0, output_tokens: 0 } },
})}\n\n`;

/** Mirror of the Rust test helper: a jwt whose payload carries the auth claim. */
function jwtWithAuthClaim(accountId: string, fedramp: boolean): string {
  const payload = Buffer.from(
    JSON.stringify({
      "https://api.openai.com/auth": {
        chatgpt_account_id: accountId,
        chatgpt_account_is_fedramp: fedramp,
      },
    }),
  ).toString("base64url");
  return `header.${payload}.signature`;
}

/** Run one canned exchange and hand back the recorded call. */
async function recordCall(connection: ProviderConnection, sse: string) {
  const stub = fetchStub([() => sseResponse(sse)]);
  await collect(
    createLlmClient(connection, { retry: NO_RETRY, fetch: stub.fetch })
      .streamMessage(buildLlmRequest({ model: "m" })),
  );
  expect(stub.calls).toHaveLength(1);
  return stub.calls[0];
}

describe("provider connections", () => {
  it("defaults base urls per profile and wraps secrets at parse", () => {
    const anthropic = ProviderConnectionSchema.parse({
      provider: "anthropic_api",
      api_key: "a-key",
    });
    if (anthropic.provider !== "anthropic_api") throw new Error("wrong arm");
    expect(anthropic.base_url).toBe("https://api.anthropic.com");
    expect(anthropic.api_key).toBeInstanceOf(SecretString);
    expect(anthropic.api_key.expose()).toBe("a-key");
    expect(JSON.stringify(anthropic)).not.toContain("a-key");

    const openai = ProviderConnectionSchema.parse({
      provider: "openai_api",
      api_key: "o-key",
    });
    if (openai.provider !== "openai_api") throw new Error("wrong arm");
    expect(openai.base_url).toBe("https://api.openai.com/v1");
    expect(openai.api_key.expose()).toBe("o-key");

    const claude = ProviderConnectionSchema.parse({
      provider: "claude_coding_plan",
      access_token: "c-tok",
    });
    if (claude.provider !== "claude_coding_plan") throw new Error("wrong arm");
    expect(claude.base_url).toBe("https://api.anthropic.com");
    expect(claude.access_token.expose()).toBe("c-tok");

    const codex = ProviderConnectionSchema.parse({
      provider: "codex_coding_plan",
      access_token: "x-tok",
    });
    if (codex.provider !== "codex_coding_plan") throw new Error("wrong arm");
    expect(codex.base_url).toBe("https://chatgpt.com/backend-api/codex");
    expect(codex.access_token.expose()).toBe("x-tok");
  });

  it("accepts an already-wrapped secret and requires the credential", () => {
    const wrapped = new SecretString("pre-wrapped");
    const parsed = ProviderConnectionSchema.parse({
      provider: "anthropic_api",
      api_key: wrapped,
    });
    if (parsed.provider !== "anthropic_api") throw new Error("wrong arm");
    expect(parsed.api_key.expose()).toBe("pre-wrapped");
    expect(
      ProviderConnectionSchema.safeParse({ provider: "anthropic_api" }).success,
      "api_key is required",
    ).toBe(false);
    expect(
      ProviderConnectionSchema.safeParse({ provider: "unknown", api_key: "k" })
        .success,
      "unknown provider ids are rejected",
    ).toBe(false);
  });
});

describe("createLlmClient profile selection", () => {
  it("binds anthropic_api to the messages wire with x-api-key access", async () => {
    const call = await recordCall(
      { provider: "anthropic_api", api_key: "a-key" },
      ANTHROPIC_STOP,
    );
    expect(call.url).toBe("https://api.anthropic.com/v1/messages");
    const headers = new Headers(call.init?.headers);
    expect(headers.get("x-api-key")).toBe("a-key");
    expect(headers.get("authorization"), "no bearer on api-key access").toBeNull();
  });

  it("binds openai_api to the responses wire with bearer access", async () => {
    const call = await recordCall(
      { provider: "openai_api", api_key: "o-key" },
      OPENAI_STOP,
    );
    expect(call.url).toBe("https://api.openai.com/v1/responses");
    expect(new Headers(call.init?.headers).get("authorization")).toBe(
      "Bearer o-key",
    );
  });

  it("binds claude_coding_plan to the messages wire with oauth bearer, identity headers, and the system prefix", async () => {
    const call = await recordCall(
      { provider: "claude_coding_plan", access_token: "oauth-tok" },
      ANTHROPIC_STOP,
    );
    expect(call.url).toBe("https://api.anthropic.com/v1/messages");
    const headers = new Headers(call.init?.headers);
    expect(headers.get("authorization"), "oauth bearer").toBe("Bearer oauth-tok");
    expect(headers.get("x-api-key"), "no api key on oauth access").toBeNull();
    expect(headers.get("anthropic-beta")).toBe(
      "claude-code-20250219,oauth-2025-04-20",
    );
    expect(headers.get("anthropic-dangerous-direct-browser-access")).toBe("true");
    expect(headers.get("user-agent")).toBe("claude-cli/2.1.75");
    expect(headers.get("x-app")).toBe("cli");
    const body = call.body as Record<string, unknown>;
    expect(body.system, "identity prefix as the first system block").toEqual([
      { type: "text", text: CLAUDE_CODE_PREFIX },
    ]);
  });

  it("binds codex_coding_plan to the responses wire with account headers and the codex dialect", async () => {
    const call = await recordCall(
      {
        provider: "codex_coding_plan",
        access_token: jwtWithAuthClaim("account-123", true),
      },
      OPENAI_STOP,
    );
    expect(call.url).toBe("https://chatgpt.com/backend-api/codex/responses");
    const headers = new Headers(call.init?.headers);
    expect(headers.get("authorization")?.startsWith("Bearer "), "bearer auth").toBe(true);
    expect(headers.get("chatgpt-account-id")).toBe("account-123");
    expect(headers.get("x-openai-fedramp")).toBe("true");
    const body = call.body as Record<string, unknown>;
    expect(body, "codex dialect omits the completion cap").not.toHaveProperty(
      "max_output_tokens",
    );
    expect(body.store).toBe(false);
  });

  it("omits the fedramp header for non-fedramp codex accounts", async () => {
    const call = await recordCall(
      {
        provider: "codex_coding_plan",
        access_token: jwtWithAuthClaim("account-123", false),
      },
      OPENAI_STOP,
    );
    expect(new Headers(call.init?.headers).get("x-openai-fedramp")).toBeNull();
  });

  it("honors a custom base_url on the api profiles (compatible endpoints)", async () => {
    const anthropicCall = await recordCall(
      {
        provider: "anthropic_api",
        base_url: "https://gateway.example/anthropic",
        api_key: "k",
      },
      ANTHROPIC_STOP,
    );
    expect(anthropicCall.url).toBe(
      "https://gateway.example/anthropic/v1/messages",
    );

    const openaiCall = await recordCall(
      {
        provider: "openai_api",
        base_url: "https://gateway.example/openai",
        api_key: "k",
      },
      OPENAI_STOP,
    );
    expect(openaiCall.url).toBe("https://gateway.example/openai/responses");
  });

  it("rejects a codex connection without usable claims as a request error at construction", () => {
    let caught: unknown;
    try {
      createLlmClient({ provider: "codex_coding_plan", access_token: "not-a-jwt" });
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ProviderError);
    expect((caught as ProviderError).kind).toBe("request");
  });
});

describe("plan/api substitutability (§4.5)", () => {
  async function replayEvents(connection: ProviderConnection, sse: string) {
    const stub = fetchStub([() => sseResponse(sse)]);
    return collect(
      createLlmClient(connection, { retry: NO_RETRY, fetch: stub.fetch })
        .streamMessage(buildLlmRequest({ model: "m" })),
    );
  }

  it("claude_coding_plan replays the anthropic fixtures identically to anthropic_api", async () => {
    for (const name of ["full", "text_tool"]) {
      const sse = fixture(`./fixtures/anthropic/${name}.sse`);
      const plan = await replayEvents(
        { provider: "claude_coding_plan", access_token: "tok" },
        sse,
      );
      const api = await replayEvents(
        { provider: "anthropic_api", api_key: "key" },
        sse,
      );
      expect(plan, `${name}.sse event sequence`).toEqual(api);
    }
  });

  it("codex_coding_plan replays the openai fixture identically to openai_api", async () => {
    const sse = fixture("./fixtures/openai/full.sse");
    const plan = await replayEvents(
      {
        provider: "codex_coding_plan",
        access_token: jwtWithAuthClaim("account-123", false),
      },
      sse,
    );
    const api = await replayEvents(
      { provider: "openai_api", api_key: "key" },
      sse,
    );
    expect(plan).toEqual(api);
  });

  it("is variant-substitutable across wires for the text+tool path", async () => {
    const openAiEvents = await replayEvents(
      { provider: "openai_api", api_key: "key" },
      fixture("./fixtures/openai/full.sse"),
    );
    const anthropicEvents = await replayEvents(
      { provider: "anthropic_api", api_key: "key" },
      fixture("./fixtures/anthropic/text_tool.sse"),
    );
    expect(openAiEvents.map((event) => event.type)).toEqual(
      anthropicEvents.map((event) => event.type),
    );
  });

  it("encode deltas between plan and api profiles are exactly the §4.1 wire options", () => {
    const request = buildLlmRequest({
      model: "m",
      system_prompt: "be terse",
      max_tokens: 64,
      messages: [{ role: "user", content: [{ type: "text", text: "hi" }] }],
      tools: [
        {
          name: "read_file",
          description: "Read a file",
          input_schema: { type: "object" },
        },
      ],
      tool_choice: { tool: "read_file" },
      reasoning_effort: "low",
    });

    const anthropicApi = encodeAnthropicRequest(request);
    const anthropicPlan = encodeAnthropicRequest(request, {
      systemPrefix: CLAUDE_CODE_PREFIX,
    });
    expect(anthropicPlan.system, "system prefix shape").toEqual([
      { type: "text", text: CLAUDE_CODE_PREFIX },
      { type: "text", text: "be terse" },
    ]);
    expect(
      { ...anthropicPlan, system: undefined },
      "anthropic delta is the system shape and nothing else",
    ).toEqual({ ...anthropicApi, system: undefined });

    const openAiPublic = encodeOpenAiRequest(request, { dialect: "public" });
    const openAiCodex = encodeOpenAiRequest(request, { dialect: "codex" });
    expect(
      openAiCodex.max_output_tokens,
      "codex omits the completion cap",
    ).toBeUndefined();
    expect(openAiCodex.tool_choice, "forced tool clamps to required").toBe(
      "required",
    );
    expect(
      { ...openAiCodex, max_output_tokens: undefined, tool_choice: undefined },
      "openai delta is the cap and the forced-tool clamp and nothing else",
    ).toEqual({
      ...openAiPublic,
      max_output_tokens: undefined,
      tool_choice: undefined,
    });
  });
});
