import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { z } from "zod";

import {
  ScriptedLlmClient,
  assistantMessage,
  complete,
  hangingTurn,
  scriptedTurn,
  textBlock,
  toolUseBlock,
  userMessage,
  type ScriptedTurn,
} from "../../src/testkit/index.js";
import type { JsonObject } from "../../src/contracts/index.js";
import type {
  BackgroundTaskCompletionContext,
  BackgroundTaskOutcome,
} from "../../src/background/index.js";
import { JsonObjectSchema } from "../../src/contracts/index.js";
import { defineTool, createAgentOutcomeFn, type HookEntry } from "../../src/tool/index.js";
import type { AgentRunHandle } from "../../src/engine/index.js";

import { createAgentSdk, type AgentSdk, type AgentSpec } from "../../src/runtime/sdk.js";

// --- fixture ----------------------------------------------------------------

interface SdkFixture {
  client: ScriptedLlmClient;
  sdk: ReturnType<typeof createAgentSdk>;
}

function sdkFixture(options: {
  turns: ScriptedTurn[];
  hooks?: HookEntry[];
  recordsDir?: string;
  taskCompletionTimeoutMs?: number;
}): SdkFixture {
  const client = new ScriptedLlmClient(options.turns);
  const sdk = createAgentSdk({
    llmClients: { scripted: { client, model: "scripted-model" } },
    ...(options.hooks && { hooks: options.hooks }),
    ...(options.recordsDir !== undefined && { recordsDir: options.recordsDir }),
    ...(options.taskCompletionTimeoutMs !== undefined && {
      taskCompletionTimeoutMs: options.taskCompletionTimeoutMs,
    }),
  });
  return { client, sdk };
}

function baseSpec(fixtureTools: AgentSpec["tools"]): Omit<AgentSpec, "agentOutcomeFn"> {
  return {
    name: "fixture-agent",
    llm: "scripted",
    systemPrompt: "You are the fixture agent.",
    tools: fixtureTools,
    maxTurns: 8,
  };
}

/**
 * A host task tool: registers a background task whose completion the test
 * controls. `silent: true` opts out of completion publication entirely.
 */
interface TaskControls {
  finish: (outcome: BackgroundTaskOutcome) => void;
  cancelled: number;
  completions: BackgroundTaskOutcome[];
  contexts: BackgroundTaskCompletionContext[];
  taskIds: string[];
}

function taskTool(options: {
  silent?: boolean;
  publish?: (outcome: BackgroundTaskOutcome) => string;
  onCompletionDelayMs?: number;
}): { tool: ReturnType<typeof defineTool<JsonObject>>; controls: TaskControls } {
  let finish!: (outcome: BackgroundTaskOutcome) => void;
  const done = new Promise<BackgroundTaskOutcome>((resolve) => {
    finish = resolve;
  });
  const controls: TaskControls = {
    finish,
    cancelled: 0,
    completions: [],
    contexts: [],
    taskIds: [],
  };
  const base = {
    toolName: "start_work",
    title: "scripted work",
    cancel: () => {
      controls.cancelled += 1;
    },
    done,
  };
  const tool = defineTool<JsonObject>({
    name: "start_work",
    description: "start background work",
    input: JsonObjectSchema,
    execute: (_input, ctx) => {
      const { taskId } = ctx.backgroundTaskSupervisor.register(
        options.silent
          ? { ...base, silent: true }
          : {
              ...base,
              onCompletion: async (outcome, completionCtx) => {
                if (options.onCompletionDelayMs !== undefined) {
                  await new Promise((resolve) =>
                    setTimeout(resolve, options.onCompletionDelayMs),
                  );
                }
                controls.completions.push(outcome);
                controls.contexts.push(completionCtx);
                if (options.publish) {
                  completionCtx.notifier.publish(options.publish(outcome), {
                    key: `work:${completionCtx.taskId}`,
                  });
                }
              },
            },
      );
      controls.taskIds.push(taskId);
      return Promise.resolve({ output: { task_id: taskId } });
    },
  });
  return { tool, controls };
}

const OUTCOME_SCHEMA = z.object({ summary: z.string() });

function submitTurn(id: string, summary: string): ScriptedTurn {
  return scriptedTurn([
    complete(assistantMessage(toolUseBlock(id, "submit_outcome", { summary }))),
  ]);
}

function textTurn(text: string): ScriptedTurn {
  return scriptedTurn([complete(assistantMessage(textBlock(text)))]);
}

function start<T = string>(sdk: AgentSdk, spec: AgentSpec<T>): AgentRunHandle<T> {
  return sdk.createAgent(spec).start({ messages: [userMessage("go")] });
}

async function until(
  condition: () => boolean,
  what: string,
  timeoutMs = 2_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

function requestTexts(client: ScriptedLlmClient): string[] {
  return client.requests.flatMap((request) =>
    request.messages.flatMap((message) =>
      message.content.flatMap((block) => (block.type === "text" ? [block.text] : [])),
    ),
  );
}

function lastToolResultContent(client: ScriptedLlmClient): string {
  const request = client.requests.at(-1);
  if (!request) throw new Error("no provider requests yet");
  const last = request.messages
    .flatMap((message) => message.content)
    .filter((block) => block.type === "tool_result")
    .at(-1);
  if (!last) throw new Error("no tool_result in the last request");
  return last.content;
}

// --- the §6 invariants -------------------------------------------------------

describe("invariant 1: totality", () => {
  it("resolves outcome() for completed, failed, and cancelled runs without ever rejecting", async () => {
    const completed = sdkFixture({ turns: [textTurn("done")] });
    const completedRun = start(completed.sdk, { ...baseSpec([]) });
    await expect(completedRun.outcome()).resolves.toMatchObject({
      status: "completed",
      outcome: "done",
    });

    const crashed = sdkFixture({ turns: [] }); // unscripted call throws inside the loop
    const crashedRun = start(crashed.sdk, { ...baseSpec([]) });
    await expect(crashedRun.outcome(), "a crashed run synthesizes failed").resolves.toMatchObject(
      { status: "failed", error: { kind: "internal" } },
    );

    const hung = sdkFixture({ turns: [hangingTurn()] });
    const hungRun = start(hung.sdk, { ...baseSpec([]) });
    hungRun.interrupt();
    await expect(hungRun.outcome()).resolves.toMatchObject({ status: "cancelled" });
    await expect(
      hungRun.outcome(),
      "outcome() stays callable after the finish",
    ).resolves.toMatchObject({ status: "cancelled" });
  });
});

describe("invariant 2: single mutator", () => {
  it("commits host state inside onSubmit, before the run finishes, and finishes with the accepted value", async () => {
    const committed: string[] = [];
    let steerableDuringCommit: boolean | undefined;
    const fixture = sdkFixture({ turns: [submitTurn("toolu_1", "ship it")] });
    const runRef: { current?: AgentRunHandle<{ summary: string }> } = {};
    const run = start<{ summary: string }>(fixture.sdk, {
      ...baseSpec([]),
      agentOutcomeFn: createAgentOutcomeFn({
        name: "submit_outcome",
        schema: OUTCOME_SCHEMA,
        onSubmit: (payload) => {
          // The finishing latch is already closed: a commit-window steer is
          // refused instead of accepted-but-dropped.
          steerableDuringCommit = runRef.current?.steer(userMessage("late redirect"));
          committed.push(payload.summary);
          return Promise.resolve({ accept: { summary: `${payload.summary} (committed)` } });
        },
      }),
    });
    runRef.current = run;
    const outcome = await run.outcome();
    expect(outcome).toMatchObject({
      status: "completed",
      outcome: { summary: "ship it (committed)" },
    });
    expect(committed, "the handler is the only writer, invoked once").toEqual(["ship it"]);
    expect(steerableDuringCommit, "no steer lands inside the commit window").toBe(false);
  });
});

describe("invariant 3: idempotent submission keying", () => {
  it("hands onSubmit a submissionId equal to the attempt's toolUseId, distinct across attempts", async () => {
    const submissionIds: string[] = [];
    const fixture = sdkFixture({
      turns: [submitTurn("toolu_a", "first"), submitTurn("toolu_b", "second")],
    });
    const run = start<{ summary: string }>(fixture.sdk, {
      ...baseSpec([]),
      agentOutcomeFn: createAgentOutcomeFn({
        name: "submit_outcome",
        schema: OUTCOME_SCHEMA,
        onSubmit: (payload, ctx) => {
          submissionIds.push(ctx.submissionId);
          return Promise.resolve(
            payload.summary === "first"
              ? { reject: "try again" }
              : { accept: payload },
          );
        },
      }),
    });
    await run.outcome();
    expect(submissionIds, "stable per attempt = the toolUseId").toEqual([
      "toolu_a",
      "toolu_b",
    ]);
  });
});

describe("invariant 4: free rejection", () => {
  it("returns {reject} to the live model as a tool error and mutates nothing", async () => {
    let mutations = 0;
    const fixture = sdkFixture({
      turns: [submitTurn("toolu_a", "draft"), submitTurn("toolu_b", "final")],
    });
    const run = start<{ summary: string }>(fixture.sdk, {
      ...baseSpec([]),
      agentOutcomeFn: createAgentOutcomeFn({
        name: "submit_outcome",
        schema: OUTCOME_SCHEMA,
        onSubmit: (payload) => {
          if (payload.summary === "draft") return Promise.resolve({ reject: "needs a number" });
          mutations += 1;
          return Promise.resolve({ accept: payload });
        },
      }),
    });
    const outcome = await run.outcome();
    expect(outcome).toMatchObject({ status: "completed", turns: 2 });
    expect(mutations, "only the accepted attempt mutated").toBe(1);
    expect(
      lastToolResultContent(fixture.client),
      "the rejection reached the live model verbatim",
    ).toBe("needs a number");
  });
});

describe("invariant 5: gate parity", () => {
  it("denies a terminal submission while a task is open and enumerates the blockers", async () => {
    const { tool, controls } = taskTool({});
    const fixture = sdkFixture({
      turns: [
        scriptedTurn([
          complete(
            assistantMessage(
              toolUseBlock("toolu_w", "start_work"),
              toolUseBlock("toolu_s", "submit_outcome", { summary: "early" }),
            ),
          ),
        ]),
        textTurn("waiting for the work"),
        submitTurn("toolu_s2", "after the work"),
      ],
    });
    let accepted = 0;
    const run = start<{ summary: string }>(fixture.sdk, {
      ...baseSpec([tool]),
      agentOutcomeFn: createAgentOutcomeFn({
        name: "submit_outcome",
        schema: OUTCOME_SCHEMA,
        onSubmit: (payload) => {
          accepted += 1;
          return Promise.resolve({ accept: payload });
        },
      }),
    });
    // Turn 2 commits bare text and parks (terminal mode never exits on
    // text); the task's removal then empty-wakes the loop, which
    // re-prompts, and turn 3 submits with the gate open.
    await until(() => fixture.client.requests.length === 2, "the parked turn");
    await new Promise((resolve) => setTimeout(resolve, 20));
    controls.finish({ status: "success", outcome: "ok" });
    const outcome = await run.outcome();
    expect(outcome).toMatchObject({ status: "completed", turns: 3 });
    expect(accepted, "the early submission never reached onSubmit").toBe(1);
    const denial = fixture.client.requests[1].messages
      .flatMap((message) => message.content)
      .filter((block) => block.type === "tool_result")
      .map((block) => block.content)
      .find((content) => content.startsWith("submission denied"));
    expect(denial, "the denial enumerates the blockers").toContain(
      "background task(s) still open",
    );
  });

  it("blocks the text exit on the same predicate (open task) and completes once it clears", async () => {
    const { tool, controls } = taskTool({ silent: true });
    const fixture = sdkFixture({
      turns: [
        scriptedTurn([complete(assistantMessage(toolUseBlock("toolu_w", "start_work")))]),
        textTurn("waiting now"),
      ],
    });
    const run = start(fixture.sdk, { ...baseSpec([tool]) });
    await until(() => fixture.client.requests.length === 2, "the post-task turn");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(controls.taskIds, "one task registered").toHaveLength(1);
    controls.finish({ status: "success", outcome: "ok" });
    const outcome = await run.outcome();
    expect(outcome, "the parked run completed with the existing text").toMatchObject({
      status: "completed",
      outcome: "waiting now",
      turns: 2,
    });
  });
});

describe("invariant 6: owed completion", () => {
  it("bounds onCompletion by taskCompletionTimeoutMs and never wedges the run", async () => {
    const hangingCompletion = taskTool({ onCompletionDelayMs: 60_000 });
    const fixture = sdkFixture({
      turns: [
        scriptedTurn([complete(assistantMessage(toolUseBlock("toolu_w", "start_work")))]),
        textTurn("all wrapped"),
      ],
      taskCompletionTimeoutMs: 30,
    });
    const run = start(fixture.sdk, { ...baseSpec([hangingCompletion.tool]) });
    await until(() => hangingCompletion.controls.taskIds.length === 1, "the task to register");
    hangingCompletion.controls.finish({ status: "success", outcome: "ok" });
    const outcome = await run.outcome();
    expect(outcome, "the timed-out handler removed the task and the run finished").toMatchObject({
      status: "completed",
      outcome: "all wrapped",
    });
  });
});

describe("invariant 7: explicit silence", () => {
  it("invokes onCompletion exactly once for handler tasks and never for silent ones", async () => {
    const handled = taskTool({});
    const fixture = sdkFixture({
      turns: [
        scriptedTurn([complete(assistantMessage(toolUseBlock("toolu_w", "start_work")))]),
        textTurn("ok"),
      ],
    });
    const run = start(fixture.sdk, { ...baseSpec([handled.tool]) });
    await until(() => handled.controls.taskIds.length === 1, "the task to register");
    handled.controls.finish({ status: "success", outcome: "result text" });
    await run.outcome();
    expect(handled.controls.completions, "exactly one invocation").toEqual([
      { status: "success", outcome: "result text" },
    ]);
    expect(handled.controls.contexts[0]?.taskId, "the handler context names the task").toBe(
      handled.controls.taskIds[0],
    );
  });
});

describe("invariant 8: completion wake", () => {
  it("wakes a parked run on a silent removal with an empty inbox and completes without another provider call", async () => {
    const { tool, controls } = taskTool({ silent: true });
    const fixture = sdkFixture({
      turns: [
        scriptedTurn([complete(assistantMessage(toolUseBlock("toolu_w", "start_work")))]),
        textTurn("standing by"),
      ],
    });
    const run = start(fixture.sdk, { ...baseSpec([tool]) });
    await until(() => fixture.client.requests.length === 2, "the parked turn");
    await new Promise((resolve) => setTimeout(resolve, 20));
    controls.finish({ status: "success", outcome: "quiet" });
    const outcome = await run.outcome();
    expect(outcome).toMatchObject({ status: "completed", outcome: "standing by" });
    expect(
      fixture.client.requests,
      "the empty wake burned no provider call",
    ).toHaveLength(2);
  });
});

describe("invariant 9: cancel race", () => {
  it("cancel() returns true only for a running task; a completed task is not found", async () => {
    const { tool, controls } = taskTool({});
    const fixture = sdkFixture({
      turns: [
        scriptedTurn([complete(assistantMessage(toolUseBlock("toolu_w", "start_work")))]),
        textTurn("ok"),
      ],
    });
    const run = start(fixture.sdk, { ...baseSpec([tool]) });
    await until(() => controls.taskIds.length === 1, "the task to register");
    const taskId = controls.taskIds[0] as never;
    expect(await run.backgroundTaskSupervisor.cancel(taskId), "running → true").toBe(true);
    expect(controls.cancelled, "teardown ran").toBe(1);
    expect(
      await run.backgroundTaskSupervisor.cancel(taskId),
      "already removed → false",
    ).toBe(false);
    expect(controls.completions[0]?.status, "one completion path, cancelled").toBe(
      "cancelled",
    );
    await run.outcome();
  });
});

describe("invariant 10: lossless records", () => {
  it("writes every lifecycle event from seq 0 and the conversation artifact, with no events() consumer", async () => {
    const recordsDir = mkdtempSync(join(tmpdir(), "eos-records-"));
    const { tool, controls } = taskTool({ publish: () => "work finished" });
    const fixture = sdkFixture({
      turns: [
        scriptedTurn([complete(assistantMessage(toolUseBlock("toolu_w", "start_work")))]),
        textTurn("waiting"),
        textTurn("recorded"),
      ],
      recordsDir,
    });
    const run = start(fixture.sdk, { ...baseSpec([tool]) });
    await until(() => fixture.client.requests.length === 2, "the parked turn");
    await new Promise((resolve) => setTimeout(resolve, 20));
    controls.finish({ status: "success", outcome: "ok" });
    await run.outcome();

    const runDir = join(recordsDir, run.runId);
    await until(() => {
      try {
        return readFileSync(join(runDir, "events.jsonl"), "utf8").includes("run_finished");
      } catch {
        return false;
      }
    }, "events.jsonl to flush");
    const events = readFileSync(join(runDir, "events.jsonl"), "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { seq: number; type: string });
    expect(events[0], "lossless from the first line").toMatchObject({
      seq: 0,
      type: "run_started",
    });
    const types = events.map((event) => event.type);
    for (const expected of [
      "turn_started",
      "tool_execution_started",
      "tool_execution_completed",
      "task_registered",
      "task_settled",
      "run_finished",
    ]) {
      expect(types, `events.jsonl carries ${expected}`).toContain(expected);
    }
    const seqs = events.map((event) => event.seq);
    expect(seqs, "event order preserved").toEqual([...seqs].sort((a, b) => a - b));

    const messages = readFileSync(join(runDir, "messages.jsonl"), "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { seq: number; kind: string });
    expect(messages.map((line) => line.kind)).toEqual([
      "user",
      "assistant",
      "tool_results",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(messages.map((line) => line.seq)).toEqual([0, 1, 2, 3, 4, 5]);
  });
});

describe("invariant 11: exhaustive inbox", () => {
  it("injects nothing itself: a silent task leaves no notification, a publishing handler exactly one", async () => {
    const silent = taskTool({ silent: true });
    const silentFixture = sdkFixture({
      turns: [
        scriptedTurn([complete(assistantMessage(toolUseBlock("toolu_w", "start_work")))]),
        textTurn("quiet run"),
      ],
    });
    const silentRun = start(silentFixture.sdk, { ...baseSpec([silent.tool]) });
    await until(() => silent.controls.taskIds.length === 1, "the silent task");
    await new Promise((resolve) => setTimeout(resolve, 20));
    silent.controls.finish({ status: "success", outcome: "invisible" });
    await silentRun.outcome();
    expect(
      requestTexts(silentFixture.client).filter((text) =>
        text.includes("<system_notification>"),
      ),
      "no SDK-originated inbox message",
    ).toEqual([]);

    const publishing = taskTool({ publish: () => "host says: finished" });
    const publishingFixture = sdkFixture({
      turns: [
        scriptedTurn([complete(assistantMessage(toolUseBlock("toolu_w", "start_work")))]),
        textTurn("waiting"),
        textTurn("saw the note"),
      ],
    });
    const publishingRun = start(publishingFixture.sdk, {
      ...baseSpec([publishing.tool]),
    });
    await until(() => publishingFixture.client.requests.length === 2, "the parked turn");
    await new Promise((resolve) => setTimeout(resolve, 20));
    publishing.controls.finish({ status: "success", outcome: "ok" });
    await publishingRun.outcome();
    const notes = requestTexts(publishingFixture.client).filter((text) =>
      text.includes("<system_notification>"),
    );
    expect(notes, "exactly the host's publish").toHaveLength(1);
    expect(notes[0]).toContain("host says: finished");
  });
});

describe("invariant 12: run-end disposal", () => {
  it("cancels running tasks on interrupt, runs their handlers with cancelled, and empties the registry", async () => {
    const { tool, controls } = taskTool({ publish: () => "never delivered" });
    const fixture = sdkFixture({
      turns: [
        scriptedTurn([complete(assistantMessage(toolUseBlock("toolu_w", "start_work")))]),
        hangingTurn(),
      ],
    });
    const run = start(fixture.sdk, { ...baseSpec([tool]) });
    await until(() => controls.taskIds.length === 1, "the task to register");
    run.interrupt();
    await expect(run.outcome()).resolves.toMatchObject({ status: "cancelled" });
    await until(
      () => run.backgroundTaskSupervisor.list().length === 0,
      "disposal to settle the task",
    );
    expect(controls.cancelled, "the task's teardown ran").toBe(1);
    expect(controls.completions, "one completion, status cancelled").toMatchObject([
      { status: "cancelled" },
    ]);
  });
});

describe("invariant 13: one channel per signal", () => {
  it("keeps hook decisions on the tool-result channel and notifier content out of results", async () => {
    const noisy = defineTool({
      name: "noisy",
      description: "publishes while running",
      input: z.object({}),
      execute: (_input, ctx) => {
        ctx.notifier.publish("side note from the host");
        return Promise.resolve({ output: "clean result" });
      },
    });
    const fixture = sdkFixture({
      turns: [
        scriptedTurn([
          complete(
            assistantMessage(
              toolUseBlock("toolu_1", "noisy"),
              toolUseBlock("toolu_2", "blocked_tool"),
            ),
          ),
        ]),
        textTurn("done"),
      ],
      hooks: [
        {
          event: "preToolUse",
          matcher: { toolName: "blocked_tool" },
          run: () => ({ decision: "deny", reason: "denied by policy hook" }),
        },
      ],
    });
    const blocked = defineTool({
      name: "blocked_tool",
      description: "always denied",
      input: z.object({}),
      execute: () => Promise.resolve({ output: "never runs" }),
    });
    const run = start(fixture.sdk, { ...baseSpec([noisy, blocked]) });
    await run.outcome();
    const results = fixture.client.requests[1].messages
      .flatMap((message) => message.content)
      .filter((block) => block.type === "tool_result");
    expect(results.map((block) => block.content), "deny rides the result; publish does not").toEqual([
      "clean result",
      "denied by policy hook",
    ]);
    const notes = requestTexts(fixture.client).filter((text) =>
      text.includes("<system_notification>"),
    );
    expect(notes, "the publish rode the inbox").toHaveLength(1);
    expect(notes[0]).toContain("side note from the host");
    expect(notes[0], "no hook reason in the inbox").not.toContain("denied by policy hook");
  });
});
