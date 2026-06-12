import { describe, expect, it } from "vitest";

import {
  assistant,
  complete,
  flushMicrotasks,
  hanging,
  scripted,
  startLoop,
  text,
  toolUse,
  user,
} from "./support.js";

describe("runAgentLoop text mode", () => {
  it("completes on a bare-text turn when the gate is open", async () => {
    const run = startLoop({
      turns: [scripted([complete(assistant(text("all done")))])],
    });
    const outcome = await run.handle.outcome();
    expect(outcome).toMatchObject({ status: "completed", outcome: "all done", turns: 1 });
    expect(outcome.usage.input_tokens).toBe(1);
  });

  it("keeps the run open while the task registry is non-empty, then completes with the existing text on a silent removal", async () => {
    const run = startLoop({
      turns: [scripted([complete(assistant(text("waiting on tasks")))])],
    });
    run.tasks.setOpen(1);
    await flushMicrotasks();
    expect(run.handle.finished, "registry non-empty blocks the text exit").toBe(false);

    run.tasks.setOpen(0); // the empty wake: a silent task removal
    const outcome = await run.handle.outcome();
    expect(outcome, "the run completes with the existing final text").toMatchObject({
      status: "completed",
      outcome: "waiting on tasks",
      turns: 1,
    });
  });

  it("wakes a parked run on a publish and re-prompts with the rendered notification", async () => {
    const run = startLoop({
      turns: [
        scripted([complete(assistant(text("parked")))]),
        scripted([complete(assistant(text("saw it")))]),
      ],
    });
    run.tasks.setOpen(1);
    await flushMicrotasks();
    expect(run.client.requests, "the run parked after turn 1").toHaveLength(1);
    run.inbox.publish("task finished: ok");
    await flushMicrotasks();
    expect(run.client.requests, "the publish woke the park").toHaveLength(2);
    run.tasks.setOpen(0);
    const outcome = await run.handle.outcome();
    expect(outcome).toMatchObject({ status: "completed", outcome: "saw it", turns: 2 });
    const last = run.client.requests[1].messages.at(-1);
    expect(last?.role).toBe("user");
    const block = last?.content[0];
    expect(block?.type === "text" ? block.text : "").toContain(
      '<system_notification>{"message":"task finished: ok"}</system_notification>',
    );
  });

  it("blocks the text exit on an undrained notification and drains it next boundary", async () => {
    const run = startLoop({
      turns: [
        scripted([complete(assistant(text("first answer")))]),
        scripted([complete(assistant(text("final answer")))]),
      ],
    });
    // Lands while turn 1 streams: the exit gate's `inbox drained` conjunct
    // closes, the boundary drains it, and the model sees it on turn 2.
    run.inbox.publish("one more thing");
    const outcome = await run.handle.outcome();
    expect(outcome, "the run continued past the closed gate").toMatchObject({
      status: "completed",
      outcome: "final answer",
      turns: 2,
    });
  });

  it("steers outrank notifications at the boundary drain", async () => {
    const run = startLoop({
      turns: [
        scripted([complete(assistant(text("thinking")))]),
        scripted([complete(assistant(text("done")))]),
      ],
    });
    run.inbox.publish("note");
    expect(run.handle.steer(user("redirect")), "steer accepted mid-turn").toBe(true);
    await run.handle.outcome();
    const texts = run.client.requests[1].messages.map((message) => {
      const block = message.content[0];
      return block.type === "text" ? block.text : message.role;
    });
    const steerIndex = texts.findIndex((value) => value === "redirect");
    const noteIndex = texts.findIndex((value) => value.includes("note"));
    expect(steerIndex, "the steer arrived").toBeGreaterThan(0);
    expect(noteIndex, "the notification arrived").toBeGreaterThan(0);
    expect(steerIndex, "steer appended before the notification").toBeLessThan(noteIndex);
  });

  it("fails with max_turns when the budget is spent", async () => {
    const run = startLoop({
      turns: [
        scripted([complete(assistant(toolUse("t1", "probe")))]),
        scripted([complete(assistant(toolUse("t2", "probe")))]),
      ],
      maxTurns: 2,
      tools: { probe: () => Promise.resolve({ content: "ok" }) },
    });
    const outcome = await run.handle.outcome();
    expect(outcome).toMatchObject({
      status: "failed",
      error: { kind: "max_turns" },
      turns: 2,
    });
  });
});

describe("runAgentLoop terminal mode", () => {
  it("finishes with the accepted submission when a terminal result lands", async () => {
    const run = startLoop({
      turns: [scripted([complete(assistant(toolUse("t1", "submit", { ok: true })))])],
      mode: {
        kind: "terminal",
        takeAccepted: () => ({ value: "accepted-payload" }),
      },
      tools: {
        submit: () => Promise.resolve({ content: "submission accepted", is_terminal: true }),
      },
    });
    const outcome = await run.handle.outcome();
    expect(outcome).toMatchObject({ status: "completed", outcome: "accepted-payload" });
  });

  it("re-prompts instead of completing on an empty wake", async () => {
    const run = startLoop({
      turns: [
        scripted([complete(assistant(text("just text")))]),
        scripted([complete(assistant(toolUse("t1", "submit")))]),
      ],
      mode: { kind: "terminal", takeAccepted: () => ({ value: "done" }) },
      tools: {
        submit: () => Promise.resolve({ content: "ok", is_terminal: true }),
      },
    });
    run.tasks.setOpen(1);
    await flushMicrotasks();
    expect(run.handle.finished, "bare text never terminates a terminal run").toBe(false);
    run.tasks.setOpen(0); // empty wake → the loop re-prompts the model
    const outcome = await run.handle.outcome();
    expect(outcome).toMatchObject({ status: "completed", outcome: "done", turns: 2 });
  });

  it("fails internally when a terminal result arrives without an accepted submission", async () => {
    const run = startLoop({
      turns: [scripted([complete(assistant(toolUse("t1", "submit")))])],
      mode: { kind: "terminal", takeAccepted: () => undefined },
      tools: {
        submit: () => Promise.resolve({ content: "ok", is_terminal: true }),
      },
    });
    const outcome = await run.handle.outcome();
    expect(outcome).toMatchObject({ status: "failed", error: { kind: "internal" } });
  });
});

describe("runAgentLoop lifecycle", () => {
  it("answers unanswered tool_use ids with synthetic interrupted results", async () => {
    const run = startLoop({
      turns: [
        scripted([
          complete(assistant(toolUse("t1", "probe"), toolUse("t2", "missing"))),
        ]),
        scripted([complete(assistant(text("done")))]),
      ],
      tools: { probe: () => Promise.resolve({ content: "ok" }) },
    });
    await run.handle.outcome();
    const second = run.client.requests[1];
    const results = second.messages
      .flatMap((message) => message.content)
      .filter((block) => block.type === "tool_result");
    expect(results, "every tool_use answered, in order").toHaveLength(2);
    expect(results[1]).toMatchObject({ is_error: true, content: "interrupted" });
  });

  it("classifies an interrupt as cancelled and disposes the registry", async () => {
    let started = false;
    const run = startLoop({
      turns: [
        hanging(() => {
          started = true;
        }),
      ],
    });
    await flushMicrotasks();
    expect(started).toBe(true);
    run.handle.interrupt();
    const outcome = await run.handle.outcome();
    expect(outcome).toMatchObject({ status: "cancelled" });
    expect(run.tasks.disposedWith, "run-end disposal ran").toBe("run finished");
  });

  it("interrupt during a park cancels the run", async () => {
    const run = startLoop({
      turns: [scripted([complete(assistant(text("parked")))])],
    });
    run.tasks.setOpen(1);
    await flushMicrotasks();
    run.handle.interrupt();
    const outcome = await run.handle.outcome();
    expect(outcome).toMatchObject({ status: "cancelled" });
  });

  it("classifies an unscripted provider fault as an internal failure", async () => {
    const run = startLoop({ turns: [] });
    const outcome = await run.handle.outcome();
    expect(outcome).toMatchObject({ status: "failed", error: { kind: "internal" } });
  });

  it("stamps monotonic seq on every event and ends the live stream after run_finished", async () => {
    const run = startLoop({
      turns: [scripted([complete(assistant(text("done")))])],
    });
    const seen: number[] = [];
    for await (const event of run.handle.events()) {
      seen.push(event.seq);
    }
    expect(seen.length).toBeGreaterThan(0);
    expect(seen, "seq strictly increases").toEqual([...seen].sort((a, b) => a - b));
    expect(run.events.at(-1)?.type, "run_finished is last").toBe("run_finished");
    expect(() => run.handle.events()[Symbol.asyncIterator](), "single consumer").toThrow();
  });

  it("memoizes outcome() and refuses steers after finish", async () => {
    const run = startLoop({
      turns: [scripted([complete(assistant(text("done")))])],
    });
    const first = run.handle.outcome();
    const second = run.handle.outcome();
    expect(first, "outcome() is memoized").toBe(second);
    await first;
    expect(run.handle.steer(user("late")), "steer after finish").toBe(false);
  });

  it("records the conversation artifact through the sink", async () => {
    const run = startLoop({
      turns: [scripted([complete(assistant(text("done")))])],
    });
    await run.handle.outcome();
    expect(run.records.map((entry) => entry.kind)).toEqual(["user", "assistant"]);
  });

  it("hands turn facts to the boundary hook", async () => {
    const seen: number[] = [];
    const run = startLoop({
      turns: [
        scripted([complete(assistant(toolUse("t1", "probe")))]),
        scripted([complete(assistant(text("done")))]),
      ],
      tools: { probe: () => Promise.resolve({ content: "ok" }) },
      onTurnBoundary: (facts) => {
        seen.push(facts.toolCalls);
        return Promise.resolve();
      },
    });
    await run.handle.outcome();
    expect(seen, "one boundary per non-finishing committed turn").toEqual([1]);
  });
});
