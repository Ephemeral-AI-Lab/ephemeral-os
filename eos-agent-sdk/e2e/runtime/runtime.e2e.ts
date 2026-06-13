import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { z } from "zod";

import {
  createAgentOutcomeFn,
  createAgentSdk,
  defineTool,
  type AgentEvent,
  type BackgroundTaskOutcome,
  type HookEntry,
  type ToolCallFacts,
  type ToolDefinition,
  type ToolResult,
  type UserMessage,
} from "../../src/index.js";
import { loadConfiguredCodexClient } from "../llm-client/support/llm-clients-config.js";

const codex = loadConfiguredCodexClient();
const RUNTIME_CLIENT_ID = "runtime_codex";
const CODEWORD = "zebra-7-runtime";

const RuntimeOutcomeSchema = z.object({
  status: z.literal("completed"),
  codeword: z.string(),
});

type RuntimeOutcome = z.infer<typeof RuntimeOutcomeSchema>;

if (!codex.available) {
  console.warn(`runtime e2e skipped: ${codex.reason}`);
}

function config() {
  if (!codex.available) {
    throw new Error("unreachable: the suite is skipped without credentials");
  }
  return codex;
}

function userMessage(text: string): UserMessage {
  return { role: "user", content: [{ type: "text", text }] };
}

function runtimeRecordsDir(): string {
  return mkdtempSync(join(tmpdir(), "eos-sdk-runtime-e2e-"));
}

function createRuntimeSdk(options: {
  recordsDir: string;
  hooks?: HookEntry[];
}) {
  const clientConfig = config();
  return createAgentSdk({
    llmClients: {
      [RUNTIME_CLIENT_ID]: {
        client: clientConfig.createClient(),
        model: clientConfig.model,
        reasoningEffort: clientConfig.reasoningEffort,
      },
    },
    recordsDir: options.recordsDir,
    ...(options.hooks !== undefined && { hooks: options.hooks }),
  });
}

function runtimeOutcomeFn() {
  return createAgentOutcomeFn({
    name: "submit_runtime_outcome",
    description:
      "Finish the runtime e2e by submitting {status:'completed', codeword}.",
    schema: RuntimeOutcomeSchema,
  });
}

function runtimeSystemPrompt(): string {
  return [
    "You are a terse runtime E2E agent.",
    "Follow the user's numbered instructions exactly and in order.",
    "Make at most one tool call per assistant turn.",
    "Do not write prose unless explicitly asked to wait.",
  ].join(" ");
}

function collectEvents(run: { events(): AsyncIterable<AgentEvent> }): {
  events: AgentEvent[];
  done: Promise<void>;
} {
  const events: AgentEvent[] = [];
  const done = (async () => {
    for await (const event of run.events()) {
      events.push(event);
    }
  })();
  return { events, done };
}

async function readJsonlUntil(
  path: string,
  done: (records: unknown[]) => boolean,
): Promise<unknown[]> {
  const deadline = Date.now() + 2_000;
  let lastError: unknown;
  while (Date.now() < deadline) {
    try {
      const raw = readFileSync(path, "utf8").trim();
      const records =
        raw.length === 0
          ? []
          : raw.split("\n").map((line) => JSON.parse(line) as unknown);
      if (done(records)) return records;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

function recordType(record: unknown): string | undefined {
  return typeof record === "object" &&
    record !== null &&
    "type" in record &&
    typeof record.type === "string"
    ? record.type
    : undefined;
}

function sawToolEvent(events: AgentEvent[], name: string): boolean {
  return events.some(
    (event) => event.type === "tool_execution_completed" && event.name === name,
  );
}

describe.skipIf(!codex.available)("sdk runtime over live codex (e2e)", () => {
  it(
    "runs a custom tool through pre/post hooks and completes with a terminal outcome",
    { timeout: 180_000 },
    async () => {
      const recordsDir = runtimeRecordsDir();
      const preCalls: ToolCallFacts[] = [];
      const postCalls: { call: ToolCallFacts; result: ToolResult }[] = [];
      let lookupContext:
        | {
            toolUseId: string;
            llmMessages: number;
            sameSupervisor: boolean;
          }
        | undefined;

      const lookupCodeword: ToolDefinition = defineTool({
        name: "lookup_codeword",
        description:
          "Return the runtime e2e codeword. Takes {} and returns {codeword}.",
        input: z.object({}),
        execute: (_input, ctx) => {
          lookupContext = {
            toolUseId: ctx.toolUseId,
            llmMessages: ctx.llmMessages.length,
            sameSupervisor:
              ctx.backgroundTaskSupervisor === run.backgroundTaskSupervisor,
          };
          return Promise.resolve({ output: { codeword: CODEWORD } });
        },
      });

      const hooks: HookEntry[] = [
        {
          event: "preToolUse",
          run: (call) => {
            preCalls.push(call);
            return { decision: "passthrough" };
          },
        },
        {
          event: "postToolUse",
          matcher: { toolName: "lookup_codeword" },
          run: (call, result) => {
            postCalls.push({ call, result });
            return { decision: "passthrough" };
          },
        },
      ];

      const sdk = createRuntimeSdk({ recordsDir, hooks });
      const agent = sdk.createAgent<RuntimeOutcome>({
        name: "runtime-live",
        llm: RUNTIME_CLIENT_ID,
        systemPrompt: runtimeSystemPrompt(),
        tools: [lookupCodeword],
        agentOutcomeFn: runtimeOutcomeFn(),
        maxTurns: 5,
      });

      const run = agent.start({
        messages: [
          userMessage(
            [
              "1. Call lookup_codeword with {}.",
              `2. After it returns, call submit_runtime_outcome with {"status":"completed","codeword":"${CODEWORD}"}.`,
              "Do not guess. Do not write final prose.",
            ].join("\n"),
          ),
        ],
      });
      const collected = collectEvents(run);

      const outcome = await run.outcome();
      await collected.done;

      expect(outcome).toMatchObject({
        status: "completed",
        outcome: { status: "completed", codeword: CODEWORD },
      });
      expect(lookupContext, "custom tool received SDK call context").toBeDefined();
      expect(lookupContext?.sameSupervisor).toBe(true);
      expect(lookupContext?.llmMessages, "tool saw conversation snapshot").toBeGreaterThan(
        0,
      );
      expect(
        preCalls.map((call) => call.toolName),
        "pre hooks observed ordinary and terminal tools",
      ).toEqual(expect.arrayContaining(["lookup_codeword", "submit_runtime_outcome"]));
      expect(postCalls, "post hook observed the custom tool result").toHaveLength(1);
      expect(postCalls[0]?.result).toMatchObject({
        output: { codeword: CODEWORD },
      });
      expect(sawToolEvent(collected.events, "lookup_codeword")).toBe(true);
      expect(sawToolEvent(collected.events, "submit_runtime_outcome")).toBe(true);

      const runDir = join(recordsDir, run.runId);
      const recordEvents = await readJsonlUntil(join(runDir, "events.jsonl"), (records) =>
        records.some((record) => recordType(record) === "run_finished"),
      );
      const recordMessages = await readJsonlUntil(
        join(runDir, "messages.jsonl"),
        (records) => records.length > 0,
      );
      expect(
        recordEvents.some((event) => recordType(event) === "run_finished"),
        "records include the terminal lifecycle event",
      ).toBe(true);
      expect(recordMessages.length, "records include conversation messages").toBeGreaterThan(
        0,
      );
    },
  );

  it(
    "parks on background work, wakes on host notification, and then submits",
    { timeout: 180_000 },
    async () => {
      const recordsDir = runtimeRecordsDir();
      const boundaryTurns: number[] = [];
      const completions: BackgroundTaskOutcome[] = [];
      let scheduled = false;

      const startBackgroundWork = defineTool({
        name: "start_background_work",
        description:
          "Start background work. Takes {} and publishes a completion notification when ready.",
        input: z.object({}),
        execute: (_input, ctx) => {
          const done = new Promise<BackgroundTaskOutcome>((resolve) => {
            if (!scheduled) {
              scheduled = true;
              setTimeout(() => {
                resolve({ status: "success", outcome: "ready" });
              }, 200);
            }
          });
          const { taskId } = ctx.backgroundTaskSupervisor.register({
            tag: { type: "runtime_e2e", id: "background" },
            title: "runtime background work",
            cancel: () => undefined,
            done,
            onCompletion: (outcome, completionCtx) => {
              completions.push(outcome);
              completionCtx.notifier.publish(
                `runtime work complete: ${outcome.outcome}`,
                { key: "runtime-work" },
              );
            },
          });
          return Promise.resolve({ output: { taskId } });
        },
      });

      const sdk = createRuntimeSdk({
        recordsDir,
        hooks: [
          {
            event: "turnBoundary",
            run: (turn) => {
              boundaryTurns.push(turn.turn);
            },
          },
        ],
      });
      const agent = sdk.createAgent<RuntimeOutcome>({
        name: "runtime-background-live",
        llm: RUNTIME_CLIENT_ID,
        systemPrompt: runtimeSystemPrompt(),
        tools: [startBackgroundWork],
        agentOutcomeFn: runtimeOutcomeFn(),
        maxTurns: 6,
      });
      const run = agent.start({
        messages: [
          userMessage(
            [
              "1. Call start_background_work with {}.",
              '2. If you have not received the notification "runtime work complete: ready", reply exactly "waiting" with no tool calls.',
              `3. After you receive that notification, call submit_runtime_outcome with {"status":"completed","codeword":"background-ready"}.`,
              "Do not write final prose.",
            ].join("\n"),
          ),
        ],
      });
      const collected = collectEvents(run);

      const outcome = await run.outcome();
      await collected.done;

      expect(outcome).toMatchObject({
        status: "completed",
        outcome: { status: "completed", codeword: "background-ready" },
      });
      expect(completions, "completion handler ran once").toEqual([
        { status: "success", outcome: "ready" },
      ]);
      expect(
        boundaryTurns.length,
        "turnBoundary hook observed at least one non-finishing turn",
      ).toBeGreaterThan(0);
      expect(sawToolEvent(collected.events, "start_background_work")).toBe(true);
      expect(
        collected.events.some((event) => event.type === "task_registered"),
        "background task registration is an SDK event",
      ).toBe(true);
      expect(
        collected.events.some((event) => event.type === "task_settled"),
        "background task settlement is an SDK event",
      ).toBe(true);
    },
  );
});
