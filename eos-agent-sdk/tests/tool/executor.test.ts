import { describe, expect, it } from "vitest";
import { z } from "zod";

import { defineTool } from "../../src/tool/define.js";
import { createAgentOutcomeFn } from "../../src/tool/outcome.js";
import { buildToolExecutor } from "../../src/tool/toolset.js";
import type { TerminalGate } from "../../src/tool/terminal.js";
import { batchFixture, call, scopeFixture } from "./support.js";

const OPEN_GATE: TerminalGate = {
  blockers: () => [],
  beginFinishing: () => undefined,
  cancelFinishing: () => undefined,
};

function probeTool(name: string, log: string[], delayMs = 0) {
  return defineTool({
    name,
    description: name,
    input: z.object({}),
    execute: async () => {
      log.push(`start:${name}`);
      if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
      log.push(`end:${name}`);
      return { output: `${name} ok` };
    },
  });
}

describe("toolBatchExecutor via buildToolExecutor", () => {
  it("returns results in tool_use order and emits started/completed per call", async () => {
    const log: string[] = [];
    const { scope } = scopeFixture();
    const built = buildToolExecutor({
      scope,
      tools: [probeTool("alpha", log), probeTool("beta", log)],
    });
    const batch = batchFixture();
    const results = await built.executor.executeBatch(
      [call("t1", "beta"), call("t2", "alpha")],
      batch,
    );
    expect(results.map((result) => result.tool_use_id)).toEqual(["t1", "t2"]);
    expect(results.map((result) => result.content)).toEqual(["beta ok", "alpha ok"]);
    const types = batch.events.map((event) => event.type);
    expect(types.filter((type) => type === "tool_execution_started")).toHaveLength(2);
    expect(types.filter((type) => type === "tool_execution_completed")).toHaveLength(2);
  });

  it("maps unknown tool names to error results", async () => {
    const { scope } = scopeFixture();
    const built = buildToolExecutor({ scope, tools: [] });
    const [result] = await built.executor.executeBatch(
      [call("t1", "ghost")],
      batchFixture(),
    );
    expect(result).toMatchObject({ is_error: true, content: "tool not found: ghost" });
  });

  it("executes the terminal call after every sibling has resolved", async () => {
    const log: string[] = [];
    const { scope } = scopeFixture();
    const built = buildToolExecutor({
      scope,
      tools: [probeTool("slow", log, 20)],
      outcome: {
        fn: createAgentOutcomeFn({
          name: "submit_outcome",
          schema: z.object({}),
          onSubmit: () => {
            log.push("submit");
            return Promise.resolve({ accept: {} });
          },
        }),
        gate: OPEN_GATE,
      },
    });
    const results = await built.executor.executeBatch(
      [call("t1", "submit_outcome"), call("t2", "slow")],
      batchFixture(),
    );
    expect(log, "the sibling finished before the submission ran").toEqual([
      "start:slow",
      "end:slow",
      "submit",
    ]);
    expect(results[0].is_terminal, "the terminal result keeps its slot").toBe(true);
    expect(built.takeAccepted?.()).toEqual({ value: {} });
  });

  it("lists every tool spec sorted by name, including the terminal tool", () => {
    const log: string[] = [];
    const { scope } = scopeFixture();
    const built = buildToolExecutor({
      scope,
      tools: [probeTool("zeta", log), probeTool("alpha", log)],
      outcome: {
        fn: createAgentOutcomeFn({ name: "submit_outcome", schema: z.object({}) }),
        gate: OPEN_GATE,
      },
    });
    expect(built.executor.specs().map((spec) => spec.name)).toEqual([
      "alpha",
      "submit_outcome",
      "zeta",
    ]);
  });

  it("settles an aborted batch with synthetic interrupted results", async () => {
    const controller = new AbortController();
    const { scope } = scopeFixture();
    const blocker = defineTool({
      name: "blocker",
      description: "aborts mid-flight",
      input: z.object({}),
      execute: () => {
        controller.abort();
        return new Promise(() => undefined);
      },
    });
    const built = buildToolExecutor({ scope, tools: [blocker] });
    const results = await built.executor.executeBatch(
      [call("t1", "blocker"), call("t2", "blocker")],
      batchFixture({ signal: controller.signal }),
    );
    expect(results, "every call answered").toHaveLength(2);
    for (const result of results) {
      expect(result, `result ${result.tool_use_id}`).toMatchObject({
        is_error: true,
        content: "interrupted",
      });
    }
  });
});
