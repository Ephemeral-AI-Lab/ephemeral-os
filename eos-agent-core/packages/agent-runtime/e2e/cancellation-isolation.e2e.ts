import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { toolUses } from "@eos/contracts";

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
  SLEEPER_BODY,
  TERSE_BODY,
  finishedRun,
  runOf,
  runtimeFixture,
  submissionOf,
  toolResultsIn,
  until,
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

/** Park = a bare-text assistant turn after the spawn, with the session live. */
async function untilParked(transcriptPath: string, label: string): Promise<void> {
  await until(
    label,
    () => {
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
    },
    120_000,
  );
}

// Budget guard: ~21 small provider calls across three scenarios; two
// scenarios interrupt mid-park over the mocked wait window, the third is a
// short guided cycle run. Assertions are structural - statuses, reasons,
// ids, transcript needles - never model prose.
describe.skipIf(!codex.available)("cancellation isolation over live codex (e2e)", () => {
  it(
    "isolates cancellation and background sessions between two live main runs (E2E-57)",
    { timeout: 420_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "north",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 6,
            body: TERSE_BODY,
          },
          {
            name: "south",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent", "cancel_background_session"],
            maxTurns: 8,
            body: TERSE_BODY,
          },
          {
            name: "sleeper_n",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["wait"],
            maxTurns: 3,
            body: SLEEPER_BODY,
          },
          {
            name: "sleeper_s",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["wait"],
            maxTurns: 3,
            body: SLEEPER_BODY,
          },
        ],
        baseTools: [wait.definition],
      });
      const parkPrompt = (sleeper: string): string =>
        [
          `1. Call run_subagent with agent_name "${sleeper}" and prompt "hold".`,
          '2. Reply with the plain text "standing by" and make no further tool calls until a new user instruction arrives.',
        ].join("\n");
      const north = runtime.startRun({
        agentName: "north",
        initialMessages: [userMessage(parkPrompt("sleeper_n"))],
      });
      const south = runtime.startRun({
        agentName: "south",
        initialMessages: [userMessage(parkPrompt("sleeper_s"))],
      });
      await untilParked(north.transcriptPath, "north to park on its sleeper");
      await untilParked(south.transcriptPath, "south to park on its sleeper");

      north.handle.interrupt("operator_stop");
      const northOutcome = await north.handle.outcome;
      if (northOutcome.status !== "cancelled") {
        throw new Error(`expected a cancelled outcome, got ${northOutcome.status}`);
      }
      expect(northOutcome.reason).toBe("operator_stop");
      const sleeperNorth = await finishedRun(runtime, "sleeper_n", 120_000);
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, sleeperNorth.run_id)).at(-1)),
        "north's child died with north",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "caller_disposed",
      });

      expect(
        must(runOf(runtime, "south")).status,
        "the sibling main run is untouched by north's cancellation",
      ).toBe("running");
      expect(
        must(runOf(runtime, "sleeper_s")).status,
        "the sibling's child session is untouched by north's disposal",
      ).toBe("running");

      expect(
        south.handle.steer(
          userMessage(
            [
              "New instruction:",
              '1. Call cancel_background_session with type "subagent" and id set to the run_id returned by run_subagent.',
              '2. Call submit_main_outcome with summary "south done".',
            ].join("\n"),
          ),
        ),
        "the surviving run still accepts work",
      ).toBe(true);
      const southOutcome = await south.handle.outcome;
      expect(southOutcome.status, "the sibling completes after the other run died").toBe(
        "completed",
      );
      expect(asString(submissionOf(southOutcome).summary)).toContain("south done");
      const sleeperSouth = await finishedRun(runtime, "sleeper_s", 120_000);
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, sleeperSouth.run_id)).at(-1)),
        "the sibling's child ended by its own run's explicit cancel, not the cascade",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "model_cancelled",
      });

      const northRaw = readFileSync(north.transcriptPath, "utf8");
      const southRaw = readFileSync(south.transcriptPath, "utf8");
      expect(northRaw, "no cross-run id leaked into north").not.toContain(south.runId);
      expect(northRaw, "no cross-run session leaked into north").not.toContain(
        sleeperSouth.run_id,
      );
      expect(southRaw, "no cross-run id leaked into south").not.toContain(north.runId);
      expect(southRaw, "no cross-run session leaked into south").not.toContain(
        sleeperNorth.run_id,
      );
    },
  );

  it(
    "cancels every background session of one run on disposal (E2E-58)",
    { timeout: 300_000 },
    async () => {
      const wait = waitTool();
      const sleepers = ["s1", "s2", "s3"];
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "marshal",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 8,
            body: TERSE_BODY,
          },
          ...sleepers.map((name) => ({
            name,
            kind: "subagent" as const,
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["wait"],
            maxTurns: 3,
            body: SLEEPER_BODY,
          })),
        ],
        baseTools: [wait.definition],
      });
      const marshal = runtime.startRun({
        agentName: "marshal",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "s1" and prompt "hold".',
              '2. Call run_subagent with agent_name "s2" and prompt "hold".',
              '3. Call run_subagent with agent_name "s3" and prompt "hold".',
              '4. Reply with the plain text "holding" and make no further tool calls until a new user instruction arrives.',
            ].join("\n"),
          ),
        ],
      });
      await until(
        "all three sleepers to be registered and running",
        () => sleepers.every((name) => runOf(runtime, name)?.status === "running"),
        180_000,
      );
      marshal.handle.interrupt("operator_stop");

      const outcome = await marshal.handle.outcome;
      if (outcome.status !== "cancelled") {
        throw new Error(`expected a cancelled outcome, got ${outcome.status}`);
      }
      expect(outcome.reason).toBe("operator_stop");

      for (const name of sleepers) {
        const row = await finishedRun(runtime, name, 120_000);
        expect(row.parent, `${name} belongs to the marshal`).toBe(marshal.runId);
        expect(
          must(readTranscriptLines(runTranscriptPath(dataDir, row.run_id)).at(-1)),
          `${name} was cancelled by the disposal fanout`,
        ).toMatchObject({
          kind: "run_finished",
          outcome_status: "cancelled",
          interrupt_reason: "caller_disposed",
        });
      }
      expect(
        runtime.listRuns().filter((row) => row.status !== "finished"),
        "no live run survived the disposal",
      ).toHaveLength(0);
    },
  );

  it(
    "leaves no stale sessions across repeated start/cancel cycles (E2E-59)",
    { timeout: 360_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "cycler",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: [
              "run_subagent",
              "list_background_sessions",
              "cancel_background_session",
            ],
            maxTurns: 14,
            body: TERSE_BODY,
          },
          ...["s1", "s2"].map((name) => ({
            name,
            kind: "subagent" as const,
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["wait"],
            maxTurns: 3,
            body: SLEEPER_BODY,
          })),
        ],
        baseTools: [wait.definition],
      });
      const run = runtime.startRun({
        agentName: "cycler",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "s1" and prompt "hold".',
              '2. Call cancel_background_session with type "subagent" and id set to the run_id from step 1.',
              "3. Call list_background_sessions.",
              '4. Call run_subagent with agent_name "s2" and prompt "hold".',
              '5. Call cancel_background_session with type "subagent" and id set to the run_id from step 4.',
              "6. Call list_background_sessions.",
              '7. Call submit_main_outcome with summary "cycles clean".',
            ].join("\n"),
          ),
        ],
      });
      const outcome = await run.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("cycles clean");

      const emptyLists = toolResultsIn(outcome.llm).filter(
        (result) => !result.is_error && result.content === "[]",
      );
      expect(
        emptyLists.length,
        "each cycle's list call saw no stale session after cancel + drain",
      ).toBeGreaterThanOrEqual(2);

      const first = await finishedRun(runtime, "s1", 120_000);
      const second = await finishedRun(runtime, "s2", 120_000);
      expect(first.run_id, "each cycle minted a distinct run").not.toBe(second.run_id);
      for (const row of [first, second]) {
        expect(
          must(readTranscriptLines(runTranscriptPath(dataDir, row.run_id)).at(-1)),
          `${row.agent_name} ended as a model-initiated cancel in its own transcript`,
        ).toMatchObject({
          kind: "run_finished",
          outcome_status: "cancelled",
          interrupt_reason: "model_cancelled",
        });
      }
      expect(
        runtime
          .listRuns()
          .filter((row) => ["cycler", "s1", "s2"].includes(row.agent_name)),
        "the registry holds the main run and two child sessions",
      ).toHaveLength(3);
    },
  );
});
