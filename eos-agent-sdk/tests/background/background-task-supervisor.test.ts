import { describe, expect, it } from "vitest";

import { mintAgentRunId } from "../../src/contracts/index.js";
import type { Notifier } from "../../src/notification/index.js";

import { RunBackgroundTaskSupervisor } from "../../src/background/background-task-supervisor.js";
import type {
  BackgroundTask,
  BackgroundTaskCompletionContext,
  BackgroundTaskLifecycleEvent,
  BackgroundTaskOutcome,
} from "../../src/background/background-task.js";

interface Fixture {
  supervisor: RunBackgroundTaskSupervisor;
  events: BackgroundTaskLifecycleEvent[];
  published: string[];
}

function fixture(completionTimeoutMs = 200): Fixture {
  const events: BackgroundTaskLifecycleEvent[] = [];
  const published: string[] = [];
  const notifier: Notifier = {
    publish: (message) => {
      published.push(message);
    },
  };
  const supervisor = new RunBackgroundTaskSupervisor({
    runId: mintAgentRunId(),
    notifier,
    completionTimeoutMs,
    emit: (event) => {
      events.push(event);
    },
  });
  return { supervisor, events, published };
}

interface ScriptedTask {
  task: BackgroundTask;
  finish: (outcome: BackgroundTaskOutcome) => void;
  fail: (error: Error) => void;
  cancelled: number;
  completions: { outcome: BackgroundTaskOutcome; ctx: BackgroundTaskCompletionContext }[];
}

function scriptedTask(
  init: {
    silent?: boolean;
    onCompletion?: (
      outcome: BackgroundTaskOutcome,
      ctx: BackgroundTaskCompletionContext,
    ) => void | Promise<void>;
  } = {},
): ScriptedTask {
  let finish!: (outcome: BackgroundTaskOutcome) => void;
  let fail!: (error: Error) => void;
  const done = new Promise<BackgroundTaskOutcome>((resolve, reject) => {
    finish = resolve;
    fail = reject;
  });
  const record = {
    finish,
    fail,
    cancelled: 0,
    completions: [] as ScriptedTask["completions"],
  };
  const base = {
    toolName: "exec_command",
    title: "scripted task",
    cancel: () => {
      record.cancelled += 1;
    },
    done,
  };
  const task: BackgroundTask = init.silent
    ? { ...base, silent: true as const }
    : {
        ...base,
        onCompletion:
          init.onCompletion ??
          ((outcome, ctx) => {
            record.completions.push({ outcome, ctx });
          }),
      };
  return Object.assign(record, { task });
}

async function settled(): Promise<void> {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

describe("RunBackgroundTaskSupervisor", () => {
  it("registers a task, lists it, and removes it once onCompletion finishes", async () => {
    const { supervisor, events } = fixture();
    const scripted = scriptedTask();
    const { taskId } = supervisor.register(scripted.task);

    expect(supervisor.list(), "registered task is listed").toEqual([
      expect.objectContaining({ taskId, toolName: "exec_command", title: "scripted task" }),
    ]);
    expect(events[0]).toMatchObject({
      type: "task_registered",
      task: { taskId },
    });

    scripted.finish({ status: "success", outcome: "done" });
    await settled();

    expect(scripted.completions, "handler invoked exactly once").toHaveLength(1);
    expect(scripted.completions[0].outcome).toEqual({ status: "success", outcome: "done" });
    expect(scripted.completions[0].ctx.taskId).toBe(taskId);
    expect(supervisor.list(), "completed task is removed").toEqual([]);
    expect(events[1]).toEqual({
      type: "task_settled",
      taskId,
      toolName: "exec_command",
      title: "scripted task",
      outcome: { status: "success", outcome: "done" },
    });
  });

  it("maps a rejecting done promise to a failed outcome", async () => {
    const { supervisor, events } = fixture();
    const scripted = scriptedTask();
    supervisor.register(scripted.task);
    scripted.fail(new Error("boom"));
    await settled();
    expect(scripted.completions[0].outcome).toEqual({ status: "failed", outcome: "boom" });
    expect(events.at(-1)).toMatchObject({
      type: "task_settled",
      outcome: { status: "failed", outcome: "boom" },
    });
  });

  it("removes a silent task immediately with no handler and no publication", async () => {
    const { supervisor, events, published } = fixture();
    const scripted = scriptedTask({ silent: true });
    supervisor.register(scripted.task);
    scripted.finish({ status: "success", outcome: "quiet" });
    await settled();
    expect(supervisor.list()).toEqual([]);
    expect(published, "the supervisor never publishes").toEqual([]);
    expect(events.at(-1), "the settled event is still durable history").toMatchObject({
      type: "task_settled",
      outcome: { status: "success", outcome: "quiet" },
    });
  });

  it("records a throwing onCompletion on the settled event and still removes the task", async () => {
    const { supervisor, events } = fixture();
    const scripted = scriptedTask({
      onCompletion: () => {
        throw new Error("handler exploded");
      },
    });
    supervisor.register(scripted.task);
    scripted.finish({ status: "success", outcome: "ok" });
    await settled();
    expect(supervisor.list(), "a broken handler never wedges the registry").toEqual([]);
    expect(events.at(-1)).toMatchObject({
      type: "task_settled",
      completionError: "handler exploded",
    });
  });

  it("bounds onCompletion by completionTimeoutMs", async () => {
    const { supervisor, events } = fixture(20);
    const scripted = scriptedTask({
      onCompletion: () => new Promise<void>(() => undefined),
    });
    supervisor.register(scripted.task);
    scripted.finish({ status: "success", outcome: "ok" });
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(supervisor.list(), "a hung handler is timed out and removed").toEqual([]);
    expect(events.at(-1)).toMatchObject({
      type: "task_settled",
      completionError: "onCompletion exceeded 20ms",
    });
  });

  it("cancel() transitions a running task, runs teardown, and completes as cancelled", async () => {
    const { supervisor } = fixture();
    const scripted = scriptedTask();
    const { taskId } = supervisor.register(scripted.task);
    expect(await supervisor.cancel(taskId)).toBe(true);
    expect(scripted.cancelled, "task.cancel() ran").toBe(1);
    expect(scripted.completions[0].outcome).toEqual({
      status: "cancelled",
      outcome: "cancelled",
    });
    expect(supervisor.list()).toEqual([]);
    scripted.finish({ status: "success", outcome: "late" });
    await settled();
    expect(scripted.completions, "the late natural settle is dropped").toHaveLength(1);
  });

  it("cancel() returns false for unknown and already-completed tasks", async () => {
    const { supervisor } = fixture();
    const scripted = scriptedTask();
    const { taskId } = supervisor.register(scripted.task);
    scripted.finish({ status: "success", outcome: "done" });
    await settled();
    expect(await supervisor.cancel(taskId), "completed task is not found").toBe(false);
    expect(scripted.completions, "no second completion").toHaveLength(1);
  });

  it("disposeAll cancels survivors, runs handlers with the reason, and empties the registry", async () => {
    const { supervisor } = fixture();
    const first = scriptedTask();
    const second = scriptedTask({ silent: true });
    supervisor.register(first.task);
    supervisor.register(second.task);
    await supervisor.disposeAll("run finished");
    expect(first.cancelled).toBe(1);
    expect(second.cancelled).toBe(1);
    expect(first.completions[0].outcome).toEqual({
      status: "cancelled",
      outcome: "run finished",
    });
    expect(supervisor.list(), "no task survives its run").toEqual([]);
  });

  it("cancels a registration that lands after disposal and leaves no trace", async () => {
    const { supervisor, events } = fixture();
    await supervisor.disposeAll("run finished");
    const late = scriptedTask();
    supervisor.register(late.task);
    await settled();
    expect(late.cancelled, "late registration is immediately cancelled").toBe(1);
    expect(supervisor.list()).toEqual([]);
    expect(events, "nothing is registered or emitted").toEqual([]);
  });

  it("waitForChange wakes on task removal and tolerates a capture-then-wait race", async () => {
    const { supervisor } = fixture();
    const scripted = scriptedTask({ silent: true });
    supervisor.register(scripted.task);
    const since = supervisor.changeCount();
    const wait = supervisor.waitForChange(since, new AbortController().signal);
    scripted.finish({ status: "success", outcome: "done" });
    await wait;
    expect(supervisor.isEmpty()).toBe(true);
    await supervisor.waitForChange(since, new AbortController().signal);
  });
});
