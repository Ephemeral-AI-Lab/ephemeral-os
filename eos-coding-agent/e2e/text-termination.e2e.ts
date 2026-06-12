import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

import { assistantText, toolUses, type Message } from "@eos/contracts";
import { scriptedTool } from "@eos/testkit";
import type { ToolDefinition } from "@eos/tool";

import {
  asString,
  must,
  readResultLines,
  readTranscriptLines,
  userMessage,
} from "../tests/support.js";
import { runTranscriptPath } from "../src/transcript.js";
import {
  CODEX_CLIENT_ID,
  loadConfiguredCodexRuntime,
} from "./support/codex-runtime.js";
import {
  CODEWORD,
  HOLDER_BODY,
  TERSE_BODY,
  finishedRun,
  gateTool,
  lookupCodewordTool,
  runOf,
  runtimeFixture,
  sessionSettledMessages,
  submissionOf,
  unansweredToolUses,
  until,
  userMessageIndex,
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

/** Shared body for the no-terminal profiles under test. */
const TEXT_BODY = [
  "You are a terse text-mode test agent with no submission tool.",
  "Follow the user's numbered instructions exactly and in order.",
  "Make at most one tool call per assistant turn and write no prose beyond",
  "what the instructions require.",
  "To finish the run, reply with your final answer as plain text and make no",
  "tool calls in that reply.",
].join(" ");

/** The park probe from E2E-09/45: a bare-text last assistant turn. */
function parkedOnBareText(transcriptPath: string): boolean {
  try {
    const assistants = readTranscriptLines(transcriptPath).filter(
      (line) => line.kind === "assistant",
    );
    const last = assistants.at(-1);
    return (
      assistants.length >= 2 && last !== undefined && toolUses(last.message).length === 0
    );
  } catch {
    return false;
  }
}

/** Every drained `{type:"reminder"}` notification text, in arrival order. */
function reminderTexts(llm: readonly Message[]): string[] {
  return llm
    .filter((message) => message.role === "user")
    .map((message) => assistantText(message))
    .filter((text) => text.includes('"reminder"'));
}

/** Every `submit_*` tool_use across the provider history. */
function submissionToolUses(llm: readonly Message[]): string[] {
  return llm
    .filter((message) => message.role === "assistant")
    .flatMap((message) => toolUses(message))
    .map((use) => use.name)
    .filter((name) => name.startsWith("submit_"));
}

// Budget guard: four live runs (~15 small provider calls). Every runtime
// here loads the REAL repo `.eos-agents/notification_rules.json` and
// `hooks.json` baselines; the text-mode profiles are temp fixtures (no
// in-repo production profile omits its terminal tool yet). Assertions are
// on outcomes, drained notification payloads, and message order - never
// prose.
describe.skipIf(!codex.available)("text termination mode over live codex (e2e)", () => {
  it(
    "completes a no-terminal subagent by text, untouched by reminder rules (T1)",
    { timeout: 180_000 },
    async () => {
      const lookup = lookupCodewordTool();
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "texter",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["lookup_codeword"],
            terminal: null,
            maxTurns: 6,
            body: TEXT_BODY,
          },
        ],
        baseTools: [lookup.definition],
      });
      const run = runtime.startRun({
        agentName: "texter",
        initialMessages: [
          userMessage(
            [
              "1. Call lookup_codeword.",
              "2. Reply with only the codeword as plain text and make no tool",
              "   calls in that reply; that reply ends the run.",
            ].join("\n"),
          ),
        ],
      });

      const outcome = await run.handle.outcome;
      if (outcome.status !== "completed") {
        throw new Error(`expected a completed outcome, got ${outcome.status}`);
      }
      expect(asString(outcome.submission), "the final text is the submission").toContain(
        CODEWORD,
      );
      expect(
        lookup.calls(),
        "the tool round-trip happened before the exit",
      ).toBeGreaterThanOrEqual(1);
      expect(
        submissionToolUses(outcome.llm),
        "no submit_* spec existed, so none was called",
      ).toEqual([]);
      expect(
        reminderTexts(outcome.llm),
        "the baseline rules stayed silent for the text-mode run",
      ).toEqual([]);
      expect(unansweredToolUses(outcome.llm)).toEqual([]);

      await finishedRun(runtime, "texter");
      expect(
        readResultLines(join(dirname(run.transcriptPath), "result.jsonl")),
        "the rollup records the text completion",
      ).toEqual([expect.objectContaining({ run_id: run.runId, status: "completed" })]);
    },
  );

  it(
    "delivers the child's text to the parent through session settlement (T2)",
    { timeout: 300_000 },
    async () => {
      const lookup = lookupCodewordTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "boss",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 8,
            body: TERSE_BODY,
          },
          {
            name: "texter",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["lookup_codeword"],
            terminal: null,
            maxTurns: 6,
            body: TEXT_BODY,
          },
        ],
        baseTools: [lookup.definition],
      });
      const run = runtime.startRun({
        agentName: "boss",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "texter" and prompt "Look up',
              '   the codeword and reply with only the codeword as plain text."',
              '2. Reply with the plain text "standing by" and make no further tool',
              "   calls until a session_settled notification arrives.",
              "3. After it arrives, call submit_main_outcome with summary set to",
              "   exactly the summary string from that session_settled notification.",
            ].join("\n"),
          ),
        ],
      });

      const outcome = await run.handle.outcome;
      expect(outcome.status, "the parent completed").toBe("completed");
      if (outcome.status !== "completed") throw new Error("unreachable");

      const texter = await finishedRun(runtime, "texter");
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, texter.run_id)).at(-1)),
        "the child completed by text and its submission is the codeword text",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "completed",
        submission: expect.stringContaining(CODEWORD) as unknown,
      });

      const settlement = must(sessionSettledMessages(outcome.llm).at(0));
      expect(
        assistantText(settlement),
        "the settlement summary carries the child's text",
      ).toContain(CODEWORD);
      expect(
        asString(submissionOf(outcome).summary),
        "the parent echoed the settlement summary",
      ).toContain(CODEWORD);
    },
  );

  it(
    "parks a text turn while a child runs, then finishes after the settlement (T3)",
    { timeout: 300_000 },
    async () => {
      const gate = gateTool();
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "middle",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            terminal: null,
            maxTurns: 8,
            body: TEXT_BODY,
          },
          {
            name: "holder",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["hold"],
            maxTurns: 3,
            body: HOLDER_BODY,
          },
        ],
        baseTools: [gate.definition],
      });
      const run = runtime.startRun({
        agentName: "middle",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "holder" and prompt "hold".',
              '2. Reply with the plain text "WAITING" and make no tool calls in',
              "   that reply.",
              "3. After the session_settled notification for that run arrives,",
              '   reply with the plain text "holder finished" and make no tool',
              "   calls; that reply ends the run.",
            ].join("\n"),
          ),
        ],
      });

      await until(
        "the middle agent to park on its bare-text turn",
        () => parkedOnBareText(run.transcriptPath),
        120_000,
      );
      expect(
        runOf(runtime, "middle")?.status,
        "the bare-text turn parked instead of finishing the run",
      ).toBe("running");

      await gate.started;
      gate.release();
      const outcome = await run.handle.outcome;
      if (outcome.status !== "completed") {
        throw new Error(`expected a completed outcome, got ${outcome.status}`);
      }
      expect(asString(outcome.submission), "completed by text after the wake").toContain(
        "holder finished",
      );
      const settledIndex = userMessageIndex(outcome.llm, '"session_settled"');
      expect(settledIndex, "the settlement was drained").toBeGreaterThanOrEqual(0);
      const finalMessage = must(outcome.llm.at(-1));
      expect(finalMessage.role, "the final llm entry is the finishing text").toBe(
        "assistant",
      );
      expect(toolUses(finalMessage)).toHaveLength(0);
      expect(
        settledIndex,
        "the settlement precedes the final text",
      ).toBeLessThan(outcome.llm.length - 1);
    },
  );

  it(
    "speaks text-mode wording on the budget ladder and still completes by text (T4)",
    { timeout: 240_000 },
    async () => {
      const stepTool = (name: string): ToolDefinition =>
        scriptedTool({
          name,
          description: `Run the ${name} step. Takes no arguments and returns { step }.`,
          execute: () => Promise.resolve({ content: { step: name } }),
        });
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "ladder",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["step_alpha", "step_bravo"],
            terminal: null,
            maxTurns: 4,
            body: TEXT_BODY,
          },
        ],
        baseTools: [stepTool("step_alpha"), stepTool("step_bravo")],
      });
      const run = runtime.startRun({
        agentName: "ladder",
        initialMessages: [
          userMessage(
            [
              "1. Call step_alpha.",
              "2. Call step_bravo.",
              '3. Reply with the plain text "ladder done" and make no tool calls;',
              "   that reply ends the run.",
              "Treat system notifications as informational; complete every step in order.",
            ].join("\n"),
          ),
        ],
      });

      const outcome = await run.handle.outcome;
      if (outcome.status !== "completed") {
        throw new Error(`expected a completed outcome, got ${outcome.status}`);
      }
      expect(asString(outcome.submission)).toContain("ladder done");

      const budgetReminders = reminderTexts(outcome.llm).filter((text) =>
        text.includes("% of budget"),
      );
      expect(
        budgetReminders,
        "the 50% rung (ceil(4 * 0.5) = 2) fired exactly once before the text exit",
      ).toHaveLength(1);
      const reminder = must(budgetReminders.at(0));
      expect(reminder).toContain("Turn 2 of 4 (50% of budget).");
      expect(
        reminder,
        "the null branch speaks the text-mode wording",
      ).toContain("Wrap up and finish by replying with your final answer as plain text.");
      expect(reminder, "no submit-via wording for a text-mode run").not.toContain(
        "submit via",
      );
      expect(reminder, "no literal null interpolation").not.toContain("null");
    },
  );
});
