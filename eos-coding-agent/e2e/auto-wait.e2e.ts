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
  CODEWORD,
  HOLDER_BODY,
  TERSE_BODY,
  finishedRun,
  gateTool,
  lookupCodewordTool,
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

function assistantTurns(transcriptPath: string): number {
  try {
    return readTranscriptLines(transcriptPath).filter(
      (line) => line.kind === "assistant",
    ).length;
  } catch {
    return 0;
  }
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

function sleepMs(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

// Budget guard: two live runs (~13 small provider calls). The `hold` gate
// pins every settlement to a test-chosen instant, so the park windows are
// measured wall-clock intervals and no assertion depends on model prose.
// The pair contrasts what one turn shape means in two contexts: a
// no-tool-call turn with background work parks the loop (auto-wait);
// dispatching background work never parks - the agent keeps working while
// its session runs. Both park well inside the baseline 60s idle rule, so
// the registered notification rules (04.9; applied to every agent, never
// customized per suite) stay silent here. The third historical leg - bare
// text with nothing pending spinning to max_turns - is no longer reachable
// through the runtime: the baseline rescue rule reminds and completes such
// runs (notification-triggers.e2e.ts pins that); the raw engine spin stays
// pinned by the engine unit suite.
describe.skipIf(!codex.available)("auto-wait contrast over live codex (e2e)", () => {
  it(
    "parks after dispatch once nothing else remains: a measured zero-call window, then the settlement wake submits",
    { timeout: 300_000 },
    async () => {
      const gate = gateTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "idler",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            // Generous budget: the baseline 50% budget rule must threshold
            // past the measured park turn, where its publish would wake the
            // park and break the zero-call window.
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
              "3. Wait for the session_settled notification for that run; do not poll with other tools.",
              '4. After it arrives, call submit_main_outcome with summary "idle then settled".',
            ].join("\n"),
          ),
        ],
      });

      await until(
        "the idler to park on the background session",
        () => parkedOnBareText(run.transcriptPath),
        120_000,
      );
      await gate.started;
      const turnsAtPark = assistantTurns(run.transcriptPath);
      await sleepMs(4_000);
      expect(
        assistantTurns(run.transcriptPath),
        "the park is event-driven: a 4s background window adds zero provider calls",
      ).toBe(turnsAtPark);

      gate.release();
      const outcome = await run.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("idle then settled");
      expect(
        userMessageIndex(outcome.llm, '"session_settled"'),
        "the drained settlement notification is what woke the parked loop",
      ).toBeGreaterThanOrEqual(0);
      expect(
        outcome.turns,
        "the wake costs only the advisor and submission turns - the park itself consumed none",
      ).toBeLessThanOrEqual(turnsAtPark + 3);

      const holder = await finishedRun(runtime, "holder", 120_000);
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, holder.run_id)).at(-1)),
        "the holder settled naturally - no cancel cascade was involved",
      ).toMatchObject({ kind: "run_finished", outcome_status: "completed" });
    },
  );

  it(
    "keeps working while its background session runs, parking only when nothing is left to do",
    { timeout: 300_000 },
    async () => {
      const gate = gateTool();
      const lookup = lookupCodewordTool();
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "worker",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent", "lookup_codeword"],
            // Generous budget: keeps the baseline budget-rule thresholds
            // clear of the measured park turn (see the idler note above).
            maxTurns: 12,
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
        baseTools: [gate.definition, lookup.definition],
      });
      const run = runtime.startRun({
        agentName: "worker",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "holder" and prompt "hold".',
              "2. Call lookup_codeword.",
              "3. Call lookup_codeword again.",
              '4. Reply with the plain text "standing by" and make no further tool calls.',
              "5. Wait for the session_settled notification for that run; do not poll with other tools.",
              '6. After it arrives, call submit_main_outcome with summary set to "worked then settled: " followed by the codeword.',
            ].join("\n"),
          ),
        ],
      });

      // The background session is running from the spawn until the test
      // releases the gate, so all the work below happens while background
      // work is running.
      await gate.started;
      await until(
        "both lookups to run while the background session is running",
        () => lookup.calls() >= 2,
        120_000,
      );

      await until(
        "the worker to park once nothing is left to do",
        () => parkedOnBareText(run.transcriptPath),
        120_000,
      );
      const turnsAtPark = assistantTurns(run.transcriptPath);
      expect(
        turnsAtPark,
        "the loop kept making provider turns while the background session ran - dispatching alone never parks",
      ).toBeGreaterThanOrEqual(4);
      await sleepMs(4_000);
      expect(
        assistantTurns(run.transcriptPath),
        "with nothing left to do, the same loop parks: zero provider calls across the window",
      ).toBe(turnsAtPark);

      gate.release();
      const outcome = await run.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain(CODEWORD);

      const codewordIndex = outcome.llm.findIndex(
        (message) =>
          message.role === "user" &&
          message.content.some(
            (block) => block.type === "tool_result" && block.content.includes(CODEWORD),
          ),
      );
      const settledIndex = userMessageIndex(outcome.llm, '"session_settled"');
      expect(codewordIndex, "the lookup work landed in the history").toBeGreaterThanOrEqual(0);
      expect(
        settledIndex,
        "both lookups completed while the background session was still running",
      ).toBeGreaterThan(codewordIndex);
    },
  );

});
