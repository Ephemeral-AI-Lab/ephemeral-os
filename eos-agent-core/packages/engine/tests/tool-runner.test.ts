import { describe, expect, it } from "vitest";

import type { AgentEvent } from "../src/events.js";
import { runToolBatch } from "../src/tool-runner.js";
import type { ToolOutput } from "../src/tools.js";
import {
  deferred,
  must,
  registryOf,
  tick,
  toolResultBlock,
  toolUseBlock,
} from "./support.js";

function collector(): { events: AgentEvent[]; emit: (event: AgentEvent) => void } {
  const events: AgentEvent[] = [];
  return {
    events,
    emit: (event) => {
      events.push(event);
    },
  };
}

const live = (): AbortSignal => new AbortController().signal;

describe("runToolBatch", () => {
  it("assembles results in tool_use order regardless of completion order", async () => {
    const firstRelease = deferred();
    const tools = registryOf(
      ["slow", () => firstRelease.promise.then((): ToolOutput => ({ content: "slow done" }))],
      [
        "fast",
        () => {
          firstRelease.resolve();
          return Promise.resolve({ content: "fast done" });
        },
      ],
    );
    const calls = [
      toolUseBlock("tu_slow", "slow"),
      toolUseBlock("tu_fast", "fast"),
    ];
    const { emit } = collector();
    const results = await runToolBatch(calls, tools, live(), emit);
    expect(results).toEqual([
      toolResultBlock("tu_slow", "slow done"),
      toolResultBlock("tu_fast", "fast done"),
    ]);
  });

  it("caps concurrency at 8 while executing the whole batch", async () => {
    let inflight = 0;
    let maxInflight = 0;
    const tools = registryOf([
      "probe",
      (input) => {
        inflight += 1;
        maxInflight = Math.max(maxInflight, inflight);
        return tick().then((): ToolOutput => {
          inflight -= 1;
          return { content: JSON.stringify(input.i) };
        });
      },
    ]);
    const calls = Array.from({ length: 12 }, (_, i) =>
      toolUseBlock(`tu_${String(i)}`, "probe", { i }),
    );
    const { emit } = collector();
    const results = await runToolBatch(calls, tools, live(), emit);
    expect(maxInflight).toBe(8);
    expect(results.map((block) => block.content)).toEqual(
      Array.from({ length: 12 }, (_, i) => String(i)),
    );
  });

  it("maps a thrown tool error to is_error without touching siblings", async () => {
    const tools = registryOf(
      ["boom", () => Promise.reject(new Error("kaboom"))],
      ["ok", () => Promise.resolve({ content: "fine" })],
    );
    const calls = [
      toolUseBlock("tu_a", "boom"),
      toolUseBlock("tu_b", "ok"),
    ];
    const { emit } = collector();
    const results = await runToolBatch(calls, tools, live(), emit);
    expect(results).toEqual([
      toolResultBlock("tu_a", "kaboom", true),
      toolResultBlock("tu_b", "fine"),
    ]);
  });

  it("maps an unregistered tool name to a tool-not-found error result", async () => {
    const { emit } = collector();
    const results = await runToolBatch(
      [toolUseBlock("tu_g", "ghost")],
      registryOf(),
      live(),
      emit,
    );
    expect(results).toEqual([
      toolResultBlock("tu_g", "tool not found: ghost", true),
    ]);
  });

  it("emits started and completed events per call", async () => {
    const tools = registryOf(["echo", () => Promise.resolve({ content: "out" })]);
    const { events, emit } = collector();
    await runToolBatch(
      [toolUseBlock("tu_e", "echo", { v: 1 })],
      tools,
      live(),
      emit,
    );
    expect(events).toEqual([
      {
        type: "tool_execution_started",
        tool_use_id: "tu_e",
        name: "echo",
        input: { v: 1 },
      },
      {
        type: "tool_execution_completed",
        tool_use_id: "tu_e",
        name: "echo",
        output: "out",
        is_error: false,
      },
    ]);
  });

  it("settles immediately on abort: real results kept, synthetic for the rest", async () => {
    const controller = new AbortController();
    const fastDone = deferred();
    const slowRelease = deferred();
    const tools = registryOf(
      [
        "fast",
        () => {
          fastDone.resolve();
          return Promise.resolve({ content: "fast ok" });
        },
      ],
      ["slow", () => slowRelease.promise.then((): ToolOutput => ({ content: "too late" }))],
    );
    const calls = [
      toolUseBlock("tu_fast", "fast"),
      toolUseBlock("tu_slow", "slow"),
    ];
    const { events, emit } = collector();
    const batch = runToolBatch(calls, tools, controller.signal, emit);
    await fastDone.promise;
    await tick();
    controller.abort();
    const results = await batch;
    expect(results).toEqual([
      toolResultBlock("tu_fast", "fast ok"),
      toolResultBlock("tu_slow", "interrupted", true),
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
    const controller = new AbortController();
    controller.abort();
    let dispatched = false;
    const tools = registryOf([
      "never",
      () => {
        dispatched = true;
        return Promise.resolve({ content: "unreachable" });
      },
    ]);
    const { events, emit } = collector();
    const results = await runToolBatch(
      [toolUseBlock("tu_n", "never")],
      tools,
      controller.signal,
      emit,
    );
    expect(dispatched).toBe(false);
    expect(events).toEqual([]);
    expect(results).toEqual([toolResultBlock("tu_n", "interrupted", true)]);
  });
});
