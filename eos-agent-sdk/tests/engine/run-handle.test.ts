import { describe, expect, it } from "vitest";

import { agentRunIdFrom } from "../../src/contracts/index.js";
import type { BackgroundTaskSupervisor } from "../../src/background/index.js";

import { RunHandle, type AgentEvent } from "../../src/engine/run-handle.js";

function handleFixture(): { handle: RunHandle; tapped: AgentEvent[] } {
  const tapped: AgentEvent[] = [];
  const handle = new RunHandle({
    runId: agentRunIdFrom("run-1"),
    backgroundTaskSupervisor: {} as BackgroundTaskSupervisor,
    notifier: { publish: () => undefined },
    tap: (event) => {
      tapped.push(event);
    },
  });
  return { handle, tapped };
}

const OUTCOME = {
  status: "completed",
  outcome: "done",
  usage: { input_tokens: 0, output_tokens: 0 },
  turns: 1,
} as const;

describe("RunHandle", () => {
  it("stamps consecutive seq values and taps before streaming", async () => {
    const { handle, tapped } = handleFixture();
    handle.emit({ type: "turn_started", turn: 1 });
    handle.emit({ type: "turn_started", turn: 2 });
    handle.finish(OUTCOME);
    expect(tapped.map((event) => event.seq)).toEqual([0, 1, 2]);
    const streamed: AgentEvent[] = [];
    for await (const event of handle.events()) streamed.push(event);
    expect(streamed, "the live stream saw the same events").toEqual(tapped);
    expect(streamed.at(-1)?.type).toBe("run_finished");
  });

  it("enforces a single events() consumer", () => {
    const { handle } = handleFixture();
    handle.events()[Symbol.asyncIterator]();
    expect(() => handle.events()[Symbol.asyncIterator]()).toThrow(
      /single consumer/,
    );
  });

  it("resolves outcome() exactly once and memoizes it", async () => {
    const { handle } = handleFixture();
    const promise = handle.outcome();
    expect(handle.outcome(), "memoized").toBe(promise);
    handle.finish(OUTCOME);
    handle.finish({ ...OUTCOME, outcome: "second" });
    await expect(promise).resolves.toMatchObject({ outcome: "done" });
  });

  it("rejects non-user steers with a TypeError", () => {
    const { handle } = handleFixture();
    expect(() =>
      handle.steer({ role: "assistant", content: [] } as never),
    ).toThrow(TypeError);
  });

  it("refuses steers while finishing and accepts them again after a rejection reopens the run", () => {
    const { handle } = handleFixture();
    expect(handle.steer({ role: "user", content: [] })).toBe(true);
    handle.beginFinishing();
    expect(handle.steer({ role: "user", content: [] }), "during onSubmit").toBe(false);
    handle.cancelFinishing();
    expect(handle.steer({ role: "user", content: [] }), "after a reject").toBe(true);
    handle.finish(OUTCOME);
    expect(handle.steer({ role: "user", content: [] }), "after finish").toBe(false);
  });

  it("aborts its signal on interrupt and stays interruptible-idempotent", () => {
    const { handle } = handleFixture();
    expect(handle.signal.aborted).toBe(false);
    handle.interrupt();
    handle.interrupt();
    expect(handle.signal.aborted).toBe(true);
  });

  it("drops post-finish events from the stream but still taps them for records", async () => {
    const { handle, tapped } = handleFixture();
    handle.finish(OUTCOME);
    handle.emit({
      type: "task_settled",
      taskId: "task-1" as never,
      tag: { type: "exec", id: "1" },
      title: "late",
      outcome: { status: "cancelled", outcome: "run finished" },
    });
    const streamed: AgentEvent[] = [];
    for await (const event of handle.events()) streamed.push(event);
    expect(streamed.map((event) => event.type), "stream ends at run_finished").toEqual([
      "run_finished",
    ]);
    expect(tapped.map((event) => event.type), "records keep disposal settles").toEqual([
      "run_finished",
      "task_settled",
    ]);
  });

  it("level-triggers waitForSteer and resolves it on abort", async () => {
    const { handle } = handleFixture();
    const controller = new AbortController();
    const wait = handle.waitForSteer(controller.signal);
    handle.steer({ role: "user", content: [] });
    await wait;
    const aborted = handle.waitForSteer(AbortSignal.abort());
    await aborted;
  });
});
