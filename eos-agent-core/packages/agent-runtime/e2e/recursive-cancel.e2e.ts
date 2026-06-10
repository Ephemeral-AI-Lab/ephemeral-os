import { describe, expect, it } from "vitest";

import { assistantText } from "@eos/contracts";

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
  HELPER_BODY,
  SLEEPER_BODY,
  TERSE_BODY,
  finishedRun,
  noOpenBackgroundSessionsHookEntries,
  runtimeFixture,
  submissionOf,
  toolResultsIn,
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

/** A subagent body that spawns the named child, then parks on the session. */
function relayBody(childName: string): string {
  return [
    "You are the relay. Make at most one tool call per turn and write no prose.",
    `1. Call run_subagent with agent_name "${childName}" and prompt "hold".`,
    '2. Reply with the plain text "relay standing by" and make no further tool calls until a new user instruction arrives.',
  ].join(" ");
}

const SUBAGENT_TERSE = "Make at most one tool call per turn and write no prose.";

// Budget guard: ~50 small provider calls across seven scenarios; every
// scenario runs a 2-4 deep chain of live runs and ends in deterministic
// cancellation over mocked wait windows or model-paced wait steps. All
// assertions are structural - reasons, parent links, line kinds, ids.
describe.skipIf(!codex.available)("recursive cancellation over live codex (e2e)", () => {
  it(
    "cascades a parent interrupt through a child with live background work (E2E-49)",
    { timeout: 240_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "chief",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 6,
            body: TERSE_BODY,
          },
          {
            name: "relay_a",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 4,
            body: relayBody("sleeper"),
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
        hookEntries: noOpenBackgroundSessionsHookEntries(),
      });
      const chief = runtime.startRun({
        agentName: "chief",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "relay_a" and prompt "go".',
              '2. Reply with the plain text "supervising" and make no further tool calls until a new user instruction arrives.',
            ].join("\n"),
          ),
        ],
      });
      // The grandchild's wait pins the whole chain live before the interrupt.
      await wait.started;
      chief.handle.interrupt("operator_stop");

      const outcome = await chief.handle.outcome;
      if (outcome.status !== "cancelled") {
        throw new Error(`expected a cancelled outcome, got ${outcome.status}`);
      }
      expect(outcome.reason).toBe("operator_stop");

      const relay = await finishedRun(runtime, "relay_a", 120_000);
      const sleeper = await finishedRun(runtime, "sleeper", 120_000);
      expect(relay.parent, "the relay is owned by the chief").toBe(chief.runId);
      expect(sleeper.parent, "the sleeper is owned by the relay, not the chief").toBe(
        relay.run_id,
      );
      expect(wait.aborted(), "the cascade aborted the grandchild's in-flight tool").toBeGreaterThanOrEqual(1);

      expect(must(readTranscriptLines(chief.transcriptPath).at(-1))).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "operator_stop",
      });
      for (const row of [relay, sleeper]) {
        expect(
          must(readTranscriptLines(runTranscriptPath(dataDir, row.run_id)).at(-1)),
          `${row.agent_name} was cancelled by its own caller's disposal`,
        ).toMatchObject({
          kind: "run_finished",
          outcome_status: "cancelled",
          interrupt_reason: "caller_disposed",
        });
      }
      expect(
        runtime.listRuns().filter((row) => row.status !== "finished"),
        "no live run leaked from the chain",
      ).toHaveLength(0);
    },
  );

  it(
    "lets the model cancel a child whose own background work is still running (E2E-50)",
    { timeout: 300_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "chief",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent", "wait", "cancel_background_session"],
            maxTurns: 8,
            body: TERSE_BODY,
          },
          {
            name: "relay_a",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 4,
            body: relayBody("sleeper"),
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
      const chief = runtime.startRun({
        agentName: "chief",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "relay_a" and prompt "go".',
              '2. Call wait with {"ms": 20000}.',
              '3. Call cancel_background_session with type "subagent" and id set to the run_id from step 1.',
              '4. Call submit_main_outcome with summary "branch cancelled".',
            ].join("\n"),
          ),
        ],
      });
      const outcome = await chief.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("branch cancelled");

      const relay = await finishedRun(runtime, "relay_a", 120_000);
      const sleeper = await finishedRun(runtime, "sleeper", 120_000);
      const settledIndex = userMessageIndex(outcome.llm, '"session_settled"');
      expect(settledIndex, "the chief drained one settlement for the relay").toBeGreaterThanOrEqual(0);
      const settled = assistantText(must(outcome.llm.at(settledIndex)));
      expect(settled, "the settlement names the cancelled relay").toContain(relay.run_id);
      expect(settled).toContain('"status":"cancelled"');

      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, relay.run_id)).at(-1)),
        "the directly cancelled child records the model-initiated reason",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "model_cancelled",
      });
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, sleeper.run_id)).at(-1)),
        "the grandchild is cancelled by the child's own disposal, not by the model",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "caller_disposed",
      });
      expect(wait.aborted(), "the grandchild's in-flight tool was aborted").toBeGreaterThanOrEqual(1);
    },
  );

  it(
    "disposes a failed child's own background work as caller_disposed (E2E-51)",
    { timeout: 300_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "chief",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 6,
            body: TERSE_BODY,
          },
          {
            // One spawn turn exhausts the budget: deterministic max_turns
            // failure while its own background session is still live.
            name: "doomed",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 1,
            body: relayBody("sleeper"),
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
      const chief = runtime.startRun({
        agentName: "chief",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "doomed" and prompt "go".',
              "2. Wait for the session_settled notification for that run; do not poll with other tools.",
              '3. Call submit_main_outcome with summary set to "branch " followed by the status word from that notification.',
            ].join("\n"),
          ),
        ],
      });
      const outcome = await chief.handle.outcome;
      expect(outcome.status, "the parent survives the child failure").toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("failed");

      const doomed = await finishedRun(runtime, "doomed", 120_000);
      const sleeper = await finishedRun(runtime, "sleeper", 120_000);
      const settledIndex = userMessageIndex(outcome.llm, '"session_settled"');
      expect(settledIndex).toBeGreaterThanOrEqual(0);
      expect(
        assistantText(must(outcome.llm.at(settledIndex))),
        "the settlement reports the child failure",
      ).toContain('"status":"failed"');

      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, doomed.run_id)).at(-1)),
        "the child failed on its turn budget",
      ).toMatchObject({ kind: "run_finished", outcome_status: "failed" });
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, sleeper.run_id)).at(-1)),
        "the failed child's exit path disposed its own session",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "caller_disposed",
      });
      expect(sleeper.parent).toBe(doomed.run_id);
    },
  );

  it(
    "blocks a child's submission while its own background task is open (E2E-52)",
    { timeout: 300_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "chief",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 6,
            body: TERSE_BODY,
          },
          {
            name: "eager",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent", "cancel_background_session"],
            maxTurns: 8,
            body: [
              "You are eager.",
              SUBAGENT_TERSE,
              "Follow exactly:",
              '1. Call run_subagent with agent_name "sleeper" and prompt "hold".',
              '2. Call submit_subagent_outcome with summary "too early". It will fail with an error because a session is open; that is expected.',
              '3. Call cancel_background_session with type "subagent" and id set to the run_id from step 1.',
              '4. Call submit_subagent_outcome with summary "eager done".',
            ].join(" "),
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
        hookEntries: noOpenBackgroundSessionsHookEntries(),
      });
      const chief = runtime.startRun({
        agentName: "chief",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "eager" and prompt "go".',
              "2. Wait for the session_settled notification for that run; do not poll with other tools.",
              '3. Call submit_main_outcome with summary "lead done".',
            ].join("\n"),
          ),
        ],
      });
      const outcome = await chief.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("lead done");

      const eager = await finishedRun(runtime, "eager", 120_000);
      const sleeper = await finishedRun(runtime, "sleeper", 120_000);
      const settledIndex = userMessageIndex(outcome.llm, '"session_settled"');
      expect(settledIndex).toBeGreaterThanOrEqual(0);
      const settled = assistantText(must(outcome.llm.at(settledIndex)));
      expect(settled, "the settlement names the child run").toContain(eager.run_id);
      expect(
        settled,
        "the child settled completed only after clearing its own session",
      ).toContain('"status":"completed"');

      const eagerLines = readTranscriptLines(runTranscriptPath(dataDir, eager.run_id));
      expect(
        eagerLines.some(
          (line) =>
            line.kind === "tool_result" &&
            line.result.is_error &&
            typeof line.result.content === "string" &&
            line.result.content.includes("cannot submit while"),
        ),
        "the child's early submission hit the guard one level down",
      ).toBe(true);
      expect(must(eagerLines.at(-1))).toMatchObject({
        kind: "run_finished",
        outcome_status: "completed",
      });
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, sleeper.run_id)).at(-1)),
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "model_cancelled",
      });
    },
  );

  it(
    "leaves a naturally completed grandchild alone when its parent branch is cancelled (E2E-53)",
    { timeout: 300_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "chief",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent", "wait", "cancel_background_session"],
            maxTurns: 8,
            body: TERSE_BODY,
          },
          {
            // Spawns a fast helper, then sits in a long wait: the helper's
            // settlement stays undelivered while the branch is cancelled.
            name: "lounger",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent", "wait"],
            maxTurns: 5,
            body: [
              "You are the lounger.",
              SUBAGENT_TERSE,
              "Follow exactly:",
              '1. Call run_subagent with agent_name "helper" and prompt "report in".',
              '2. Call wait with {"ms": 60000}.',
              '3. After it returns, call submit_subagent_outcome with summary "lounged".',
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
      const chief = runtime.startRun({
        agentName: "chief",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "lounger" and prompt "go".',
              '2. Call wait with {"ms": 30000}.',
              '3. Call cancel_background_session with type "subagent" and id set to the run_id from step 1.',
              '4. Call submit_main_outcome with summary "pruned".',
            ].join("\n"),
          ),
        ],
      });
      const outcome = await chief.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("pruned");

      const lounger = await finishedRun(runtime, "lounger", 120_000);
      const helper = await finishedRun(runtime, "helper", 120_000);
      expect(helper.parent).toBe(lounger.run_id);
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, lounger.run_id)).at(-1)),
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "model_cancelled",
      });

      const helperLast = must(
        readTranscriptLines(runTranscriptPath(dataDir, helper.run_id)).at(-1),
      );
      if (helperLast.kind !== "run_finished") {
        throw new Error(`expected a run_finished line, got ${helperLast.kind}`);
      }
      expect(
        helperLast.outcome_status,
        "the settled-but-undelivered grandchild kept its natural completion",
      ).toBe("completed");
      expect(
        helperLast.interrupt_reason,
        "the branch cancellation never re-cancelled the settled grandchild",
      ).toBeUndefined();
      expect(wait.aborted(), "only the lounger's wait was aborted").toBeGreaterThanOrEqual(1);
    },
  );

  it(
    "unwinds a three-deep chain strictly by ownership (E2E-54)",
    { timeout: 240_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "chief",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 6,
            body: TERSE_BODY,
          },
          {
            name: "relay_a",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 4,
            body: relayBody("relay_b"),
          },
          {
            name: "relay_b",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 4,
            body: relayBody("sleeper"),
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
      const chief = runtime.startRun({
        agentName: "chief",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "relay_a" and prompt "go".',
              '2. Reply with the plain text "supervising" and make no further tool calls until a new user instruction arrives.',
            ].join("\n"),
          ),
        ],
      });
      // The deepest descendant's wait pins all four runs live.
      await wait.started;
      chief.handle.interrupt("operator_stop");

      const outcome = await chief.handle.outcome;
      if (outcome.status !== "cancelled") {
        throw new Error(`expected a cancelled outcome, got ${outcome.status}`);
      }
      expect(outcome.reason).toBe("operator_stop");

      const relayA = await finishedRun(runtime, "relay_a", 120_000);
      const relayB = await finishedRun(runtime, "relay_b", 120_000);
      const sleeper = await finishedRun(runtime, "sleeper", 120_000);
      expect(relayA.parent, "level 1 is owned by the main run").toBe(chief.runId);
      expect(relayB.parent, "level 2 is owned by level 1").toBe(relayA.run_id);
      expect(sleeper.parent, "level 3 is owned by level 2").toBe(relayB.run_id);

      for (const row of [relayA, relayB, sleeper]) {
        const lines = readTranscriptLines(runTranscriptPath(dataDir, row.run_id));
        expect(
          lines.filter((line) => line.kind === "run_finished"),
          `${row.agent_name} closes exactly once`,
        ).toHaveLength(1);
        expect(
          must(lines.at(-1)),
          `${row.agent_name} was cancelled by its own caller's disposal`,
        ).toMatchObject({
          kind: "run_finished",
          outcome_status: "cancelled",
          interrupt_reason: "caller_disposed",
        });
      }
      expect(
        runtime.listRuns().filter((row) => row.status !== "finished"),
        "all four runs reached finished",
      ).toHaveLength(0);
      expect(runtime.listRuns()).toHaveLength(4);
    },
  );

  it(
    "cancels one nested branch without touching its sibling branch (E2E-56)",
    { timeout: 360_000 },
    async () => {
      const wait = waitTool();
      const { runtime, dataDir } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "chief",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: [
              "run_subagent",
              "wait",
              "list_background_sessions",
              "cancel_background_session",
            ],
            maxTurns: 12,
            body: TERSE_BODY,
          },
          {
            name: "relay_a",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["run_subagent"],
            maxTurns: 4,
            body: relayBody("sleeper"),
          },
          {
            name: "sleeper",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["wait"],
            maxTurns: 3,
            body: SLEEPER_BODY,
          },
          {
            name: "spare",
            kind: "subagent",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["wait"],
            maxTurns: 3,
            body: SLEEPER_BODY,
          },
        ],
        baseTools: [wait.definition],
      });
      const chief = runtime.startRun({
        agentName: "chief",
        initialMessages: [
          userMessage(
            [
              '1. Call run_subagent with agent_name "relay_a" and prompt "go".',
              '2. Call run_subagent with agent_name "spare" and prompt "hold".',
              '3. Call wait with {"ms": 15000}.',
              '4. Call cancel_background_session with type "subagent" and id set to the run_id from step 1.',
              "5. Call list_background_sessions.",
              '6. Call cancel_background_session with type "subagent" and id set to the run_id from step 2.',
              '7. Call submit_main_outcome with summary "branches handled".',
            ].join("\n"),
          ),
        ],
      });
      const outcome = await chief.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("branches handled");

      const relay = await finishedRun(runtime, "relay_a", 120_000);
      const sleeper = await finishedRun(runtime, "sleeper", 120_000);
      const spare = await finishedRun(runtime, "spare", 120_000);

      // The session-list result is the JSON array; the spawn result also
      // carries the spare's id but as a bare { run_id } object.
      const listResult = must(
        toolResultsIn(outcome.llm).find(
          (result) =>
            !result.is_error &&
            result.content.startsWith("[") &&
            result.content.includes(spare.run_id),
        ),
      );
      expect(
        listResult.content,
        "after the branch cancel the sibling session is still listed as running",
      ).toContain('"status":"running"');
      expect(
        listResult.content,
        "the cancelled branch is already delivered and evicted from the list",
      ).not.toContain(relay.run_id);

      expect(
        outcome.llm.filter(
          (message) =>
            message.role === "user" && assistantText(message).includes('"session_settled"'),
        ),
        "exactly two settlements reached the chief: one per branch",
      ).toHaveLength(2);

      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, relay.run_id)).at(-1)),
        "the cancelled branch head records the model-initiated reason",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "model_cancelled",
      });
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, sleeper.run_id)).at(-1)),
        "the cancelled branch's child is disposed by its own caller",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "caller_disposed",
      });
      expect(
        must(readTranscriptLines(runTranscriptPath(dataDir, spare.run_id)).at(-1)),
        "the sibling branch outlived the cancel and ended by its own explicit cancel",
      ).toMatchObject({
        kind: "run_finished",
        outcome_status: "cancelled",
        interrupt_reason: "model_cancelled",
      });
    },
  );
});
