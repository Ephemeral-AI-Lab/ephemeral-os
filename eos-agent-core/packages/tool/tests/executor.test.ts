import { describe, expect, it } from "vitest";

import type { ToolExecutor } from "@eos/engine";
import { scriptedRunState, scriptedTool } from "@eos/testkit";

import type { ToolDefinition } from "../src/contract.js";
import { toolBatchExecutor } from "../src/executor.js";
import { HookEngine } from "../src/hooks/runner.js";
import { bindTool } from "../src/pipeline.js";
import type { AgentRunState } from "../src/run-state.js";
import { collector, live, must, resultContent, tick, toolUse } from "./support.js";

function executorOf(
  runState: AgentRunState,
  definitions: ToolDefinition[],
): ToolExecutor {
  const hooks = new HookEngine([]);
  return toolBatchExecutor({
    runState,
    tools: definitions.map((definition) => bindTool(definition, { hooks })),
  });
}

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("toolBatchExecutor", () => {
  it("assembles results in tool_use order regardless of completion order", async () => {
    const firstRelease = deferred();
    const slow = scriptedTool({
      name: "slow",
      execute: () => firstRelease.promise.then(() => ({ content: "slow done" })),
    });
    const fast = scriptedTool({
      name: "fast",
      execute: () => {
        firstRelease.resolve();
        return Promise.resolve({ content: "fast done" });
      },
    });
    const executor = executorOf(scriptedRunState(), [slow, fast]);
    const { emit } = collector();
    const results = await executor.executeBatch(
      [toolUse("tu_slow", "slow"), toolUse("tu_fast", "fast")],
      live(),
      emit,
    );
    expect(results.map((result) => [result.tool_use_id, result.content])).toEqual([
      ["tu_slow", "slow done"],
      ["tu_fast", "fast done"],
    ]);
  });

  it("caps concurrency at 8 while executing the whole batch", async () => {
    let inflight = 0;
    let maxInflight = 0;
    const probe = scriptedTool({
      name: "probe",
      execute: (input) => {
        inflight += 1;
        maxInflight = Math.max(maxInflight, inflight);
        return tick().then(() => {
          inflight -= 1;
          return { content: JSON.stringify(input.i) };
        });
      },
    });
    const executor = executorOf(scriptedRunState(), [probe]);
    const calls = Array.from({ length: 12 }, (_, i) =>
      toolUse(`tu_${String(i)}`, "probe", { i }),
    );
    const { emit } = collector();
    const results = await executor.executeBatch(calls, live(), emit);
    expect(maxInflight).toBe(8);
    expect(results.map((result) => result.content)).toEqual(
      Array.from({ length: 12 }, (_, i) => String(i)),
    );
  });

  it("maps a thrown tool error to is_error without touching siblings", async () => {
    const boom = scriptedTool({
      name: "boom",
      execute: () => Promise.reject(new Error("kaboom")),
    });
    const ok = scriptedTool({
      name: "ok",
      execute: () => Promise.resolve({ content: "fine" }),
    });
    const executor = executorOf(scriptedRunState(), [boom, ok]);
    const { emit } = collector();
    const results = await executor.executeBatch(
      [toolUse("tu_a", "boom"), toolUse("tu_b", "ok")],
      live(),
      emit,
    );
    expect(results.map((result) => [result.content, result.is_error])).toEqual([
      ["kaboom", true],
      ["fine", false],
    ]);
  });

  it("maps an unregistered tool name to a tool-not-found error result", async () => {
    const executor = executorOf(scriptedRunState(), []);
    const { emit } = collector();
    const results = await executor.executeBatch(
      [toolUse("tu_g", "ghost")],
      live(),
      emit,
    );
    expect(results).toHaveLength(1);
    expect(must(results.at(0))).toMatchObject({
      tool_use_id: "tu_g",
      content: "tool not found: ghost",
      is_error: true,
      is_terminal: false,
    });
  });

  it("emits started and completed events carrying the execution facts", async () => {
    const echo = scriptedTool({
      name: "echo",
      execute: () => Promise.resolve({ content: "out", metadata: { cost: 2 } }),
    });
    const executor = executorOf(scriptedRunState(), [echo]);
    const { events, emit } = collector();
    await executor.executeBatch([toolUse("tu_e", "echo", { v: 1 })], live(), emit);
    expect(events).toHaveLength(2);
    expect(must(events.at(0))).toEqual({
      type: "tool_execution_started",
      tool_use_id: "tu_e",
      name: "echo",
      input: { v: 1 },
    });
    const completed = must(events.at(1));
    expect(completed).toMatchObject({
      type: "tool_execution_completed",
      tool_use_id: "tu_e",
      name: "echo",
      output: "out",
      is_error: false,
      is_terminal: false,
      metadata: { cost: 2 },
    });
    if (completed.type === "tool_execution_completed") {
      expect(completed.tool_end_time).toBeGreaterThanOrEqual(
        completed.tool_start_time,
      );
    }
  });

  it("settles immediately on abort: real results kept, synthetic for the rest", async () => {
    const controller = new AbortController();
    const fastDone = deferred();
    const slowRelease = deferred();
    const fast = scriptedTool({
      name: "fast",
      execute: () => {
        fastDone.resolve();
        return Promise.resolve({ content: "fast ok" });
      },
    });
    const slow = scriptedTool({
      name: "slow",
      execute: () => slowRelease.promise.then(() => ({ content: "too late" })),
    });
    const executor = executorOf(scriptedRunState(), [fast, slow]);
    const { events, emit } = collector();
    const batch = executor.executeBatch(
      [toolUse("tu_fast", "fast"), toolUse("tu_slow", "slow")],
      controller.signal,
      emit,
    );
    await fastDone.promise;
    await tick();
    controller.abort();
    const results = await batch;
    expect(results.map((result) => [resultContent(result), result.is_error])).toEqual([
      ["fast ok", false],
      ["interrupted", true],
    ]);
    slowRelease.resolve();
    await tick();
    const completions = events.filter(
      (event) => event.type === "tool_execution_completed",
    );
    expect(
      completions,
      "a straggler settling later must not emit after the batch closed",
    ).toHaveLength(1);
    expect(must(completions.at(0))).toMatchObject({ tool_use_id: "tu_fast" });
  });

  it("dispatches nothing when the signal is already aborted", async () => {
    let dispatched = false;
    const never = scriptedTool({
      name: "never",
      execute: () => {
        dispatched = true;
        return Promise.resolve({ content: "unreachable" });
      },
    });
    const executor = executorOf(scriptedRunState(), [never]);
    const controller = new AbortController();
    controller.abort();
    const { events, emit } = collector();
    const results = await executor.executeBatch(
      [toolUse("tu_n", "never")],
      controller.signal,
      emit,
    );
    expect(dispatched).toBe(false);
    expect(events).toEqual([]);
    expect(must(results.at(0))).toMatchObject({
      content: "interrupted",
      is_error: true,
    });
  });

  it("rejects a terminal call batched with siblings, dispatching nothing (§15.1)", async () => {
    let submitted = false;
    let echoed = false;
    const submit = scriptedTool({
      name: "submit",
      terminal: true,
      execute: () => {
        submitted = true;
        return Promise.resolve({ content: { summary: "done" } });
      },
    });
    const echo = scriptedTool({
      name: "echo",
      execute: () => {
        echoed = true;
        return Promise.resolve({ content: "ok" });
      },
    });
    const executor = executorOf(scriptedRunState(), [submit, echo]);
    const { events, emit } = collector();
    const results = await executor.executeBatch(
      [toolUse("tu_s", "submit"), toolUse("tu_e", "echo")],
      live(),
      emit,
    );
    for (const result of results) {
      expect(result.is_error, `${result.tool_use_id} is an error`).toBe(true);
      expect(result.is_terminal, `${result.tool_use_id} cannot terminate`).toBe(false);
      expect(resultContent(result)).toContain("`submit` must be called alone");
    }
    expect(submitted, "the terminal tool never ran").toBe(false);
    expect(echoed, "the sibling never ran").toBe(false);
    expect(events, "nothing dispatched, nothing emitted").toEqual([]);

    const solo = await executor.executeBatch([toolUse("tu_s2", "submit")], live(), emit);
    expect(must(solo.at(0))).toMatchObject({ is_error: false, is_terminal: true });
    expect(submitted, "a solo terminal call dispatches normally").toBe(true);
  });

  it("snapshots workspace mode once per batch, even for calls dispatched after a flip (§15.15)", async () => {
    const runState = scriptedRunState();
    const flipped = deferred();
    const isolatedSeenByProbe: boolean[] = [];
    const flip = scriptedTool({
      name: "flip",
      execute: () => {
        runState.workspace.isIsolated = true;
        flipped.resolve();
        return Promise.resolve({ content: "flipped" });
      },
    });
    const sleeper = scriptedTool({
      name: "sleeper",
      execute: () => flipped.promise.then(() => ({ content: "slept" })),
    });
    const probe = scriptedTool({
      name: "probe",
      execute: (_input, ctx) => {
        isolatedSeenByProbe.push(ctx.meta.run.workspace.is_isolated);
        return Promise.resolve({ content: "probed" });
      },
    });
    const executor = executorOf(runState, [flip, sleeper, probe]);
    const { emit } = collector();
    // Cap is 8: the probe is ninth, so it dispatches only after the flip
    // completed - and must still run under the batch's pre-flip snapshot.
    const calls = [
      toolUse("tu_flip", "flip"),
      ...Array.from({ length: 7 }, (_, i) => toolUse(`tu_s${String(i)}`, "sleeper")),
      toolUse("tu_probe", "probe"),
    ];
    const results = await executor.executeBatch(calls, live(), emit);
    expect(must(results.at(-1))).toMatchObject({
      tool_use_id: "tu_probe",
      content: "probed",
      is_error: false,
    });
    expect(isolatedSeenByProbe, "the probe saw the batch snapshot").toEqual([false]);

    const stale = await executor.executeBatch([toolUse("tu_p2", "probe")], live(), emit);
    expect(must(stale.at(0)).is_error, "the next batch denies the stale call").toBe(true);
    expect(resultContent(must(stale.at(0)))).toContain(
      "not available while the workspace is isolated",
    );
  });

  it("filters specs per turn by workspace mode (§15.14)", () => {
    const runState = scriptedRunState();
    const reader = scriptedTool({
      name: "reader",
      availableInIsolatedWorkspace: true,
      execute: () => Promise.resolve({ content: "read" }),
    });
    const writer = scriptedTool({
      name: "writer",
      execute: () => Promise.resolve({ content: "wrote" }),
    });
    const executor = executorOf(runState, [reader, writer]);
    expect(executor.specs().map((spec) => spec.name)).toEqual(["reader", "writer"]);
    runState.workspace.isIsolated = true;
    expect(
      executor.specs().map((spec) => spec.name),
      "isolated mode hides the unavailable tool from the next turn",
    ).toEqual(["reader"]);
  });
});
