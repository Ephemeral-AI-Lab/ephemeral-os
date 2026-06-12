import { describe, expect, it } from "vitest";
import { z } from "zod";

import {
  ScriptedLlmClient,
  assistantMessage,
  complete,
  scriptedTurn,
  textBlock,
  toolUseBlock,
  userMessage,
  type ScriptedTurn,
} from "../../src/testkit/index.js";
import { createAgentOutcomeFn, defineTool } from "../../src/tool/index.js";

import { createAgentSdk } from "../../src/runtime/sdk.js";
import { buildLlmClientRegistry } from "../../src/runtime/llm-clients.js";

const noop = defineTool({
  name: "noop",
  description: "does nothing",
  input: z.object({}),
  execute: () => Promise.resolve({ output: "ok" }),
});

function sdkWith(turns: ScriptedTurn[]): ReturnType<typeof createAgentSdk> {
  return createAgentSdk({
    llmClients: { scripted: { client: new ScriptedLlmClient(turns), model: "m" } },
  });
}

describe("createAgentSdk", () => {
  it("exposes exactly one method", () => {
    const sdk = sdkWith([]);
    expect(Object.keys(sdk)).toEqual(["createAgent"]);
  });

  it("rejects an unknown llm ref at createAgent, before any run", () => {
    const sdk = sdkWith([]);
    expect(() =>
      sdk.createAgent({
        name: "a",
        llm: "missing",
        systemPrompt: "x",
        tools: [],
      }),
    ).toThrow(/unknown llm ref "missing"/);
  });

  it("rejects duplicate tool names, including a collision with the terminal tool", () => {
    const sdk = sdkWith([]);
    expect(() =>
      sdk.createAgent({
        name: "a",
        llm: "scripted",
        systemPrompt: "x",
        tools: [noop, noop],
      }),
    ).toThrow(/duplicate tool name\(s\): noop/);
    expect(() =>
      sdk.createAgent({
        name: "a",
        llm: "scripted",
        systemPrompt: "x",
        tools: [noop],
        agentOutcomeFn: createAgentOutcomeFn({ name: "noop", schema: z.object({}) }),
      }),
    ).toThrow(/duplicate tool name\(s\): noop/);
  });

  it("requires at least one seed message", () => {
    const sdk = sdkWith([]);
    const agent = sdk.createAgent({
      name: "a",
      llm: "scripted",
      systemPrompt: "x",
      tools: [],
    });
    expect(() => agent.start({ messages: [] })).toThrow(TypeError);
  });

  it("runs global and per-agent hooks together", async () => {
    const client = new ScriptedLlmClient([
      scriptedTurn([
        complete(assistantMessage(toolUseBlock("t1", "noop"))),
      ]),
      scriptedTurn([complete(assistantMessage(textBlock("done")))]),
    ]);
    const order: string[] = [];
    const sdk = createAgentSdk({
      llmClients: { scripted: { client, model: "m" } },
      hooks: [
        {
          event: "preToolUse",
          run: () => {
            order.push("global");
            return { decision: "passthrough" };
          },
        },
      ],
    });
    const run = sdk
      .createAgent({
        name: "a",
        llm: "scripted",
        systemPrompt: "x",
        tools: [noop],
        hooks: [
          {
            event: "preToolUse",
            run: () => {
              order.push("agent");
              return { decision: "passthrough" };
            },
          },
        ],
      })
      .start({ messages: [userMessage("go")] });
    await run.outcome();
    expect(order.sort(), "both hook layers ran on the call").toEqual(["agent", "global"]);
  });

  it("serves concurrent runs from one reusable agent template", async () => {
    const client = new ScriptedLlmClient([
      scriptedTurn([complete(assistantMessage(textBlock("first")))]),
      scriptedTurn([complete(assistantMessage(textBlock("second")))]),
    ]);
    const sdk = createAgentSdk({
      llmClients: { scripted: { client, model: "m" } },
    });
    const agent = sdk.createAgent({
      name: "a",
      llm: "scripted",
      systemPrompt: "x",
      tools: [],
    });
    const one = agent.start({ messages: [userMessage("1")] });
    const two = agent.start({ messages: [userMessage("2")] });
    expect(one.runId, "each start mints a fresh run").not.toBe(two.runId);
    const outcomes = await Promise.all([one.outcome(), two.outcome()]);
    expect(outcomes.map((outcome) => outcome.status)).toEqual([
      "completed",
      "completed",
    ]);
  });
});

describe("buildLlmClientRegistry", () => {
  it("resolves injected clients and applies the default token cap", () => {
    const client = new ScriptedLlmClient([]);
    const registry = buildLlmClientRegistry({
      scripted: { client, model: "m", reasoningEffort: "low" },
    });
    const resolved = registry.require("scripted");
    expect(resolved.client).toBe(client);
    expect(resolved.maxTokens).toBe(32768);
    expect(resolved.reasoningEffort).toBe("low");
    expect(() => registry.require("other")).toThrow(/unknown llm ref/);
  });
});
