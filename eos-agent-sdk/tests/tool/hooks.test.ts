import { describe, expect, it } from "vitest";

import { agentRunIdFrom, toolUseIdFrom } from "../../src/contracts/index.js";

import { HookEngine, type ToolCallFacts } from "../../src/tool/hooks.js";

const FACTS: ToolCallFacts = {
  runId: agentRunIdFrom("run-1"),
  toolUseId: toolUseIdFrom("t1"),
  toolName: "write_note",
  input: { text: "hi" },
};

describe("HookEngine", () => {
  it("matches preToolUse entries by exact tool name; absent matchers match all", async () => {
    const seen: string[] = [];
    const engine = new HookEngine([
      {
        event: "preToolUse",
        matcher: { toolName: "write_note" },
        run: () => {
          seen.push("matched");
          return { decision: "passthrough" };
        },
      },
      {
        event: "preToolUse",
        matcher: { toolName: "other_tool" },
        run: () => {
          seen.push("unmatched");
          return { decision: "passthrough" };
        },
      },
      {
        event: "preToolUse",
        run: () => {
          seen.push("wildcard");
          return { decision: "passthrough" };
        },
      },
    ]);
    const decision = await engine.preToolUse(FACTS);
    expect(decision).toEqual({ decision: "passthrough" });
    expect(seen.sort()).toEqual(["matched", "wildcard"]);
  });

  it("lets any deny win and joins the reasons", async () => {
    const engine = new HookEngine([
      { event: "preToolUse", run: () => ({ decision: "deny", reason: "first" }) },
      { event: "preToolUse", run: () => ({ decision: "passthrough" }) },
      { event: "preToolUse", run: () => ({ decision: "deny", reason: "second" }) },
    ]);
    expect(await engine.preToolUse(FACTS)).toEqual({
      decision: "deny",
      reason: "first; second",
    });
  });

  it("fails closed: a throwing pre hook denies with the thrown message", async () => {
    const engine = new HookEngine([
      {
        event: "preToolUse",
        run: () => {
          throw new Error("policy backend down");
        },
      },
    ]);
    expect(await engine.preToolUse(FACTS)).toEqual({
      decision: "deny",
      reason: "policy backend down",
    });
  });

  it("hands postToolUse the executed result", async () => {
    const engine = new HookEngine([
      {
        event: "postToolUse",
        run: (_call, result) =>
          "output" in result && result.output === "secret"
            ? { decision: "deny", reason: "redacted" }
            : { decision: "passthrough" },
      },
    ]);
    expect(await engine.postToolUse(FACTS, { output: "fine" })).toEqual({
      decision: "passthrough",
    });
    expect(await engine.postToolUse(FACTS, { output: "secret" })).toEqual({
      decision: "deny",
      reason: "redacted",
    });
  });

  it("runs turnBoundary entries in order, lets them publish, and skips throwers", async () => {
    const published: string[] = [];
    const engine = new HookEngine([
      {
        event: "turnBoundary",
        run: (turn, ctx) => {
          ctx.notifier.publish(`turn ${String(turn.turn)} committed`);
        },
      },
      {
        event: "turnBoundary",
        run: () => {
          throw new Error("broken rule");
        },
      },
      {
        event: "turnBoundary",
        run: (_turn, ctx) => {
          ctx.notifier.publish("second rule");
        },
      },
    ]);
    await engine.turnBoundary(
      {
        turn: 3,
        maxTurns: 8,
        toolCalls: 0,
        backgroundTaskCount: 1,
        hasPendingSteers: false,
      },
      {
        runId: agentRunIdFrom("run-1"),
        notifier: {
          publish: (message) => {
            published.push(message);
          },
        },
      },
    );
    expect(published, "a broken hook never blocks the others").toEqual([
      "turn 3 committed",
      "second rule",
    ]);
  });
});
