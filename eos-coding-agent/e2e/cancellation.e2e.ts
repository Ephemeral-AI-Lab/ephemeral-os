import { describe, expect, it } from "vitest";

import { assistantText, toolUses, type Message } from "@eos/contracts";
import { startAgentRun, type AgentEvent, type AgentRunHandle } from "@eos/engine";
import { buildToolExecutor, type ToolDefinition } from "@eos/tool";
import { scriptedRunState } from "@eos/testkit";

import { runTranscriptPath } from "../src/transcript.js";
import {
  asString,
  must,
  readTranscriptLines,
  userMessage,
} from "../tests/support.js";
import {
  CODEX_CLIENT_ID,
  loadConfiguredCodexRuntime,
} from "./support/codex-runtime.js";
import {
  CODEWORD,
  HELPER_BODY,
  SLEEPER_BODY,
  TERSE_BODY,
  finishTaskTool,
  finishedRun,
  lookupCodewordTool,
  runtimeFixture,
  submissionOf,
  toolResultsIn,
  unansweredToolUses,
  until,
  userMessageIndex,
  waitTool,
} from "./support/fixtures.js";

const codex = loadConfiguredCodexRuntime();

if (!codex.available) {
  console.warn(`agent-runtime e2e skipped: ${codex.reason}`);
}

function llmClientsPath(): string {
  if (!codex.available) {
    throw new Error("unreachable: the suite is skipped without credentials");
  }
  return codex.llmClientsPath;
}

/** Engine-direct run for the abort windows the runtime path cannot expose. */
function liveRun(definitions: ToolDefinition[], messages: Message[]): AgentRunHandle {
  if (!codex.available) {
    throw new Error("unreachable: the suite is skipped without credentials");
  }
  const { binding } = codex;
  return startAgentRun({
    llmClient: binding.client,
    tools: buildToolExecutor({ runState: scriptedRunState("main"), definitions }),
    model: binding.model_id,
    reasoningEffort: binding.reasoning_effort,
    systemPrompt: TERSE_BODY,
    maxTurns: 4,
    initialMessages: messages,
  });
}

// Budget guard: ~16 small provider calls. E2E-41 spends none (cancelled at
// the loop top), E2E-42 aborts its only call mid-stream, E2E-43/44/45 are
// interrupted runs over mocked windows, E2E-46/47 are short guided runs.
// Every assertion is structural - statuses, reasons, line kinds, ids.
describe.skipIf(!codex.available)("cancellation lifecycle over live codex (e2e)", () => {
  it(
    "cancels at the loop top when interrupted before the first provider call (E2E-41)",
    { timeout: 60_000 },
    async () => {
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "instant",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: [],
            maxTurns: 2,
            body: TERSE_BODY,
          },
        ],
      });
      const run = runtime.startRun({
        agentName: "instant",
        initialMessages: [userMessage("Reply with one word.")],
      });
      run.handle.interrupt("operator_abort");

      const outcome = await run.handle.outcome;
      if (outcome.status !== "cancelled") {
        throw new Error(`expected a cancelled outcome, got ${outcome.status}`);
      }
      expect(outcome.reason, "the supplied reason rides the outcome").toBe("operator_abort");
      expect(outcome.turns, "no provider turn committed").toBe(0);
      expect(
        outcome.usage.input_tokens + outcome.usage.output_tokens,
        "no live spend before the loop-top check",
      ).toBe(0);
      expect(
        run.handle.steer(userMessage("too late")),
        "a steer after the abort is refused",
      ).toBe(false);

      await finishedRun(runtime, "instant");
      const lines = readTranscriptLines(run.transcriptPath);
      expect(
        lines.filter((line) => line.kind === "assistant"),
        "no assistant line was written",
      ).toHaveLength(0);
      expect(
        lines.filter((line) => line.kind === "tool_result"),
        "no tool line was written",
      ).toHaveLength(0);
      expect(
        lines.filter((line) => line.kind === "run_finished"),
        "exactly one terminal transcript line",
      ).toHaveLength(1);
      expect(must(lines.at(-1))).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "operator_abort",
      });
    },
  );

  it(
    "classifies an abort during the live provider stream as cancelled, not provider_error (E2E-42)",
    { timeout: 120_000 },
    async () => {
      const handle = liveRun(
        [finishTaskTool()],
        [
          userMessage(
            'Write one short sentence about the sea, then call finish_task with summary "sea".',
          ),
        ],
      );
      const events: AgentEvent[] = [];
      for await (const event of handle.events) {
        events.push(event);
        // The first turn_started precedes any provider output; the abort
        // lands inside the in-flight SSE request.
        if (event.type === "turn_started") handle.interrupt("mid_stream_stop");
      }
      const outcome = await handle.outcome;
      if (outcome.status !== "cancelled") {
        throw new Error(`expected a cancelled outcome, got ${outcome.status}`);
      }
      expect(
        outcome.reason,
        "the abort classified by the run signal, never as a provider failure",
      ).toBe("mid_stream_stop");
      expect(
        unansweredToolUses(outcome.llm),
        "no dangling tool_use in the salvaged history",
      ).toEqual([]);
      expect(must(events.at(-1)).type, "run_finished is last even when SSE dies on abort").toBe(
        "run_finished",
      );
      expect(
        events.filter((event) => event.type === "run_finished"),
        "run_finished appears exactly once",
      ).toHaveLength(1);
    },
  );

  it(
    "preserves the first reason when two interrupts race (E2E-43)",
    { timeout: 120_000 },
    async () => {
      const wait = waitTool();
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "twice",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["wait"],
            maxTurns: 4,
            body: TERSE_BODY,
          },
        ],
        baseTools: [wait.definition],
      });
      const run = runtime.startRun({
        agentName: "twice",
        initialMessages: [
          userMessage(
            'Call wait with {"ms": 60000}. After it returns, call submit_main_outcome with summary "waited".',
          ),
        ],
      });
      await wait.started;
      run.handle.interrupt("first_reason");
      run.handle.interrupt("second_reason");

      const outcome = await run.handle.outcome;
      if (outcome.status !== "cancelled") {
        throw new Error(`expected a cancelled outcome, got ${outcome.status}`);
      }
      expect(outcome.reason, "the first interrupt reason wins").toBe("first_reason");

      await finishedRun(runtime, "twice");
      const lines = readTranscriptLines(run.transcriptPath);
      expect(
        lines.filter((line) => line.kind === "run_finished"),
        "exactly one terminal transcript line despite the double interrupt",
      ).toHaveLength(1);
      expect(must(lines.at(-1))).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "first_reason",
      });
    },
  );

  it(
    "keeps a settled real tool result through an interrupt and restarts from it live (E2E-44)",
    { timeout: 180_000 },
    async () => {
      const lookup = lookupCodewordTool();
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "stepper",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["lookup_codeword"],
            maxTurns: 4,
            body: TERSE_BODY,
          },
        ],
        baseTools: [lookup.definition],
      });
      const run = runtime.startRun({
        agentName: "stepper",
        initialMessages: [
          userMessage(
            [
              "1. Call lookup_codeword.",
              "2. Call submit_main_outcome with summary set to exactly the codeword from the result.",
            ].join("\n"),
          ),
        ],
      });
      // The interrupt lands only after the real result settled and flushed,
      // so the salvage keeps it instead of a synthetic "interrupted" stub.
      await until(
        "the lookup result to flush",
        () => {
          try {
            return readTranscriptLines(run.transcriptPath).some(
              (line) => line.kind === "tool_result",
            );
          } catch {
            return false;
          }
        },
        60_000,
      );
      run.handle.interrupt("after_result");

      const outcome = await run.handle.outcome;
      if (outcome.status !== "cancelled") {
        throw new Error(`expected a cancelled outcome, got ${outcome.status}`);
      }
      expect(outcome.reason).toBe("after_result");
      expect(outcome.turns, "only the tool turn committed").toBe(1);
      const results = toolResultsIn(outcome.llm);
      expect(results, "the answered call is the real lookup").toHaveLength(1);
      expect(must(results.at(0)).is_error, "the salvaged result is clean, not synthetic").toBe(
        false,
      );
      expect(must(results.at(0)).content).toContain(CODEWORD);
      expect(unansweredToolUses(outcome.llm)).toEqual([]);

      await finishedRun(runtime, "stepper");
      const lines = readTranscriptLines(run.transcriptPath);
      expect(
        lines.filter((line) => line.kind === "assistant"),
        "no second assistant turn committed",
      ).toHaveLength(1);
      expect(must(lines.at(-1))).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "after_result",
      });

      // The live provider accepts the salvage AND the model consumes the
      // real result carried in it - data flows through the restart.
      const second = liveRun(
        [lookup.definition, finishTaskTool()],
        [
          ...outcome.llm,
          userMessage(
            "Call finish_task now with summary set to exactly the codeword you already looked up. Do not call lookup_codeword again.",
          ),
        ],
      );
      const resumed = await second.outcome;
      expect(resumed.status, "the salvaged real-result history restarts live").toBe("completed");
      expect(asString(submissionOf(resumed).summary)).toContain(CODEWORD);
    },
  );

  it(
    "wakes a run parked on auto-wait with an interrupt and disposes its background session (E2E-45)",
    { timeout: 300_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "keeper",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 6,
            body: TERSE_BODY,
          },
          {
            name: "sleeper",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["wait"],
            maxTurns: 3,
            body: SLEEPER_BODY,
          },
        ],
        baseTools: [wait.definition],
      });
      const run = runtime.startRun({
        agentName: "keeper",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "sleeper" and prompt "hold".',
              '2. Reply with the plain text "standing by" and make no further tool calls until a new user instruction arrives.',
            ].join("\n"),
          ),
        ],
      });
      // Park observed as in E2E-09: a bare-text assistant turn while the
      // background session is running means the loop is in waitForWake, not on the wire.
      await until(
        "the keeper to park on the background session",
        () => {
          try {
            const assistants = readTranscriptLines(run.transcriptPath).filter(
              (line) => line.kind === "assistant",
            );
            const last = assistants.at(-1);
            return (
              assistants.length >= 2 &&
              last !== undefined &&
              toolUses(last.message).length === 0
            );
          } catch {
            return false;
          }
        },
        120_000,
      );
      // Both windows pinned: the keeper is parked AND the sleeper is inside
      // its wait call, so the cascade demonstrably aborts an in-flight tool.
      await wait.started;
      const parkedTurns = readTranscriptLines(run.transcriptPath).filter(
        (line) => line.kind === "assistant",
      ).length;
      await wait.started;
      run.handle.interrupt("operator_stop");

      const outcome = await run.handle.outcome;
      if (outcome.status !== "cancelled") {
        throw new Error(`expected a cancelled outcome, got ${outcome.status}`);
      }
      expect(outcome.reason).toBe("operator_stop");
      expect(
        outcome.turns,
        "the park consumed no provider calls between the bare-text turn and the interrupt",
      ).toBe(parkedTurns);
      expect(unansweredToolUses(outcome.llm)).toEqual([]);

      const sleeper = await finishedRun(runtime, "sleeper", 120_000);
      expect(wait.aborted(), "the disposal aborted the sleeper's in-flight tool").toBeGreaterThanOrEqual(1);
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, sleeper.run_id)).at(-1)),
        "the cascade recorded caller_disposed on the sleeper",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "caller_disposed",
      });
      expect(must(readTranscriptLines(run.transcriptPath).at(-1))).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "operator_stop",
      });
    },
  );

  it(
    "recovers the live model from cancelling an unknown session (E2E-46)",
    { timeout: 300_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "janitor",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: [
              "run_subagent",
              "list_background_sessions",
              "cancel_background_session",
            ],
            maxTurns: 10,
            body: TERSE_BODY,
          },
          {
            name: "sleeper",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["wait"],
            maxTurns: 3,
            body: SLEEPER_BODY,
          },
        ],
        baseTools: [wait.definition],
      });
      const run = runtime.startRun({
        agentName: "janitor",
        initialMessages: [
          userMessage(
            [
              '1. Call cancel_background_session with type "subagent" and id "run_does_not_exist". It will fail with an error; that is expected.',
              '2. Call run_subagent with agent_name "sleeper" and prompt "begin".',
              "3. Call list_background_sessions.",
              '4. Call cancel_background_session with type "subagent" and id set to the run_id from step 2.',
              '5. Call submit_main_outcome with summary "tidy".',
            ].join("\n"),
          ),
        ],
      });
      const outcome = await run.handle.outcome;
      expect(outcome.status, "the unknown cancel never became a run-level failure").toBe(
        "completed",
      );
      expect(asString(submissionOf(outcome).summary)).toContain("tidy");

      const results = toolResultsIn(outcome.llm);
      expect(
        results.some(
          (result) => result.is_error && result.content.includes("no background session"),
        ),
        "the unknown cancel failed at tool level",
      ).toBe(true);
      expect(
        results.some(
          (result) => !result.is_error && result.content.includes("cancelled"),
        ),
        "the real cancel acknowledged",
      ).toBe(true);

      const sleeper = await finishedRun(runtime, "sleeper");
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, sleeper.run_id)).at(-1)),
        "the real session ended as a model-initiated cancel",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "model_cancelled",
      });
    },
  );

  it(
    "treats a cancel after the child settled and was delivered as a recoverable no-op (E2E-47)",
    { timeout: 300_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "courier",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent", "wait", "cancel_background_session"],
            maxTurns: 8,
            body: [
              TERSE_BODY,
              'If cancel_background_session returns "no background session", treat it as the expected result and immediately call submit_main_outcome with summary "late cancel handled".',
            ].join(" "),
          },
          {
            name: "helper",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: [],
            maxTurns: 3,
            body: HELPER_BODY,
          },
        ],
        baseTools: [wait.definition],
      });
      const run = runtime.startRun({
        agentName: "courier",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "helper" and prompt "report in".',
              '2. Call wait with {"ms": 5000} to give the helper time to finish.',
              "3. Wait for the session_settled notification for that run; do not poll with other tools.",
              "4. After the notification arrives, call cancel_background_session with type \"subagent\" and id set to the run_id from step 1. It will report the session is gone; that is expected.",
              '5. Call submit_main_outcome with summary "late cancel handled".',
            ].join("\n"),
          ),
        ],
      });
      const outcome = await run.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("late cancel handled");

      const helper = await finishedRun(runtime, "helper");
      const results = toolResultsIn(outcome.llm);
      expect(
        results.some((result) =>
          result.content.includes(`no background session subagent:${helper.run_id}`),
        ),
        "the post-delivery cancel hit the evicted-session no-op surface",
      ).toBe(true);
      const settledMessages = outcome.llm.filter(
        (message) =>
          message.role === "user" && assistantText(message).includes('"session_settled"'),
      );
      expect(
        settledMessages,
        "the late cancel published no duplicate settlement",
      ).toHaveLength(1);
      expect(
        userMessageIndex(outcome.llm, helper.run_id),
        "the one settlement names the helper run",
      ).toBeGreaterThanOrEqual(0);

      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, helper.run_id)).at(-1)),
        "the helper's natural completion was never overwritten by the late cancel",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "completed",
      });
    },
  );
});
