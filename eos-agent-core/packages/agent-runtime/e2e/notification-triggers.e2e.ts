import { describe, expect, it } from "vitest";

import { assistantText, toolUses, type Message } from "@eos/contracts";
import { scriptedTool } from "@eos/testkit";
import type { ToolDefinition } from "@eos/tool";

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
  HOLDER_BODY,
  TERSE_BODY,
  gateTool,
  runtimeFixture,
  submissionOf,
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

/** A deterministic no-arg tool: each distinct name buys one reliable turn. */
function probeStepTool(name: string): ToolDefinition {
  return scriptedTool({
    name,
    description: `Run the ${name} step. Takes no arguments and returns { step }.`,
    execute: () => Promise.resolve({ content: { step: name } }),
  });
}

// Budget guard: four live runs (~26 small provider calls, one spanning the
// baseline's 60s idle window). Every runtime here loads the REAL repo
// `.eos-agents/notification_rules.json` - the rules registered for all
// agents - and the reference rule scripts are REAL spawned node processes;
// nothing is customized per scenario. Assertions are on drained reminder
// payloads, outcome status, and message order - never prose.
describe.skipIf(!codex.available)("notification triggers over live codex (e2e)", () => {
  it(
    "rescues the drifter spin via the registered baseline rules: the TurnCompleted reminder names the terminal tool and the run completes instead of failing max_turns",
    { timeout: 240_000 },
    async () => {
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "drifter",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: [],
            maxTurns: 8,
            body: TERSE_BODY,
          },
        ],
        // No overrides: this scenario runs the repo baseline rules end to end.
      });
      const run = runtime.startRun({
        agentName: "drifter",
        initialMessages: [
          userMessage(
            [
              '1. Reply with the plain text "standing by" and make no tool calls this turn.',
              "2. Then act on any system notifications you receive.",
            ].join("\n"),
          ),
        ],
      });

      const outcome = await run.handle.outcome;
      expect(
        outcome.status,
        "the reminder rescued the run the trigger-off baseline pins as failed: max_turns",
      ).toBe("completed");
      const firstAssistant = must(
        outcome.llm.find((message) => message.role === "assistant"),
      );
      expect(toolUses(firstAssistant), "the spin happened: turn 1 was bare text").toHaveLength(0);
      const reminder = assistantText(must(outcome.llm.at(2)));
      expect(
        reminder,
        "the reminder was drained before the next provider call, right after the bare-text turn",
      ).toContain('"reminder"');
      expect(reminder).toContain('"TurnCompleted"');
      expect(reminder, "the reminder names the profile's terminal tool").toContain(
        "submit_main_outcome",
      );
    },
  );

  it(
    "wakes a held park past the baseline 60s timeout: the IdleTimeout reminder lists the running session and the model recovers by cancelling it",
    { timeout: 420_000 },
    async () => {
      const gate = gateTool();
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "idler",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent", "cancel_background_session"],
            maxTurns: 10,
            body: TERSE_BODY,
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
        agentName: "idler",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "holder" and prompt "hold".',
              '2. Reply with the plain text "standing by" and make no further tool calls.',
              "3. Wait. When a system reminder about waiting on background work arrives, call",
              '   cancel_background_session with type "subagent" and the run_id from step 1.',
              '4. Then call submit_main_outcome with summary "woke and cancelled".',
            ].join("\n"),
          ),
        ],
      });

      // The gate is never released: only the idle-wake reminder can end the
      // park, so completing at all proves the baseline timer fired and woke
      // the run after its real 60s window.
      await gate.started;
      const outcome = await run.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("woke and cancelled");

      // At least one: a model that answers a wake with bare text re-parks
      // and legitimately earns another reminder (one shot per park entry;
      // the unit suite pins the exact arm/clear/re-arm semantics).
      const reminders = reminderTexts(outcome.llm);
      expect(reminders.length, "the park outlived timeout_ms").toBeGreaterThanOrEqual(1);
      expect(must(reminders.at(0))).toContain('"IdleTimeout"');
      expect(
        must(reminders.at(0)),
        "the reminder lists the running session by its native ref",
      ).toContain("subagent:");
      expect(
        userMessageIndex(outcome.llm, '"IdleTimeout"'),
        "the reminder woke the park; the cancellation settlement came after it",
      ).toBeLessThan(userMessageIndex(outcome.llm, '"session_settled"'));
    },
  );

  it(
    "stays silent when the wake comes first: a settlement inside timeout_ms means the idle script never speaks",
    { timeout: 300_000 },
    async () => {
      const gate = gateTool();
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "idler",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 10,
            body: TERSE_BODY,
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
        // No overrides: the baseline's 60s idle rule is the one under test -
        // the gate releases within seconds, so it must never speak.
      });
      const run = runtime.startRun({
        agentName: "idler",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "holder" and prompt "hold".',
              '2. Reply with the plain text "standing by" and make no further tool calls.',
              "3. Wait for the session_settled notification for that run; do not poll with other tools.",
              '4. After it arrives, call submit_main_outcome with summary "idle then settled".',
            ].join("\n"),
          ),
        ],
      });

      await until(
        "the idler to park on the live session",
        () => parkedOnBareText(run.transcriptPath),
        120_000,
      );
      await gate.started;
      gate.release();
      const outcome = await run.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("idle then settled");
      expect(
        userMessageIndex(outcome.llm, '"session_settled"'),
        "the natural settlement woke the park",
      ).toBeGreaterThanOrEqual(0);
      expect(
        reminderTexts(outcome.llm).filter((text) => text.includes('"IdleTimeout"')),
        "the wake landed first, so the idle timer was cleared and its script never spoke",
      ).toEqual([]);
    },
  );

  it(
    "publishes the baseline budget-reminder ladder: one reminder at 50% and one at 80% of max_turns, each exactly once",
    { timeout: 240_000 },
    async () => {
      // Four distinct one-call steps walk the run across both baseline
      // thresholds (ceil(5 * 0.5) = 3, ceil(5 * 0.8) = 4) with every turn
      // shaped as a tool call, so the spin-rescue rule stays silent and the
      // only reminders in the history are the two budget rungs. The prompt
      // pins the steps against the mid-choreography 50% nudge: this
      // scenario tests the ladder's once-per-rung semantics, not reminder
      // compliance (the spin-rescue scenario owns that).
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "counter",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["step_alpha", "step_bravo", "step_charlie", "step_delta"],
            maxTurns: 5,
            body: TERSE_BODY,
          },
        ],
        baseTools: [
          probeStepTool("step_alpha"),
          probeStepTool("step_bravo"),
          probeStepTool("step_charlie"),
          probeStepTool("step_delta"),
        ],
      });
      const run = runtime.startRun({
        agentName: "counter",
        initialMessages: [
          userMessage(
            [
              "1. Call step_alpha.",
              "2. Call step_bravo.",
              "3. Call step_charlie.",
              "4. Call step_delta.",
              '5. Call submit_subagent_outcome with summary "all steps done".',
              "Treat system notifications as informational; complete every step in order.",
            ].join("\n"),
          ),
        ],
      });

      const outcome = await run.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("all steps done");
      expect(outcome.turns, "four steps plus the submission").toBe(5);

      const budgetReminders = reminderTexts(outcome.llm).filter((text) =>
        text.includes("% of budget"),
      );
      expect(
        budgetReminders,
        "equality with each rung's threshold turn, not >=: one reminder per registered percentage",
      ).toHaveLength(2);
      expect(must(budgetReminders.at(0)), "the 50% rung fired first").toContain(
        "Turn 3 of 5 (50% of budget)",
      );
      expect(must(budgetReminders.at(1)), "the 80% rung followed").toContain(
        "Turn 4 of 5 (80% of budget)",
      );
      expect(
        must(budgetReminders.at(1)),
        "the reminder names the profile's terminal tool",
      ).toContain("submit_subagent_outcome");
      expect(
        userMessageIndex(outcome.llm, "80% of budget"),
        "each rung drained after its threshold turn, before the next provider call",
      ).toBeGreaterThan(
        outcome.llm.findIndex(
          (message) =>
            message.role === "assistant" &&
            toolUses(message).some((use) => use.name === "step_delta"),
        ),
      );
    },
  );
});
