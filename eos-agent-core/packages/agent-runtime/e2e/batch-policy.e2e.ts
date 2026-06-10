import { describe, expect, it } from "vitest";

import { toolUses } from "@eos/contracts";

import { asString, must, userMessage } from "../tests/support.js";
import {
  CODEX_CLIENT_ID,
  loadConfiguredCodexRuntime,
} from "./support/codex-runtime.js";
import {
  PARALLEL_BODY,
  probeTool,
  runtimeFixture,
  submissionOf,
  toolResultsIn,
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

// Budget guard: two live runs (~5 small provider calls). These scenarios
// deliberately instruct MULTIPLE tool calls in one assistant turn (unlike
// the rest of the suite) and assert the assembled batch structure: window
// overlap, result order, whole-batch policy rejection - never prose.
describe.skipIf(!codex.available)("tool-call batches over live codex (e2e)", () => {
  it(
    "dispatches a parallel batch concurrently and answers in tool_use order",
    { timeout: 180_000 },
    async () => {
      const alpha = probeTool("probe_alpha", 1500);
      const beta = probeTool("probe_beta", 1500);
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "batcher",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["probe_alpha", "probe_beta"],
            maxTurns: 6,
            body: PARALLEL_BODY,
          },
        ],
        baseTools: [alpha.definition, beta.definition],
      });
      const run = runtime.startRun({
        agentName: "batcher",
        initialMessages: [
          userMessage(
            [
              "1. In one single assistant turn, call probe_alpha and probe_beta together - two tool calls in the same response.",
              '2. After both results arrive, call submit_main_outcome with summary "batched".',
            ].join("\n"),
          ),
        ],
      });
      const outcome = await run.handle.outcome;
      expect(outcome.status).toBe("completed");

      const batchTurn = outcome.llm.find(
        (message) => message.role === "assistant" && toolUses(message).length >= 2,
      );
      expect(batchTurn, "the model issued both calls in one turn").toBeDefined();
      const batchCalls = toolUses(must(batchTurn));
      expect(batchCalls.map((call) => call.name).sort()).toEqual([
        "probe_alpha",
        "probe_beta",
      ]);

      const ids = batchCalls.map((call) => call.tool_use_id);
      const resultsMessage = must(
        outcome.llm.find(
          (message) =>
            message.role === "user" &&
            ids.every((id) =>
              message.content.some(
                (block) => block.type === "tool_result" && block.tool_use_id === id,
              ),
            ),
        ),
      );
      expect(
        resultsMessage.content
          .filter((block) => block.type === "tool_result")
          .map((block) => block.tool_use_id),
        "ONE user message answers the batch, in tool_use order",
      ).toEqual(ids);

      expect(alpha.windows(), "alpha executed exactly once").toHaveLength(1);
      expect(beta.windows(), "beta executed exactly once").toHaveLength(1);
      const a = must(alpha.windows().at(0));
      const b = must(beta.windows().at(0));
      expect(
        Math.max(a.start, b.start) < Math.min(a.end, b.end),
        "the two executions overlapped: the batch dispatched concurrently",
      ).toBe(true);
    },
  );

  it(
    "rejects a whole batch holding a batch-forbidden tool undispatched, then allows it solo",
    { timeout: 180_000 },
    async () => {
      const beta = probeTool("probe_beta", 100, { exclusive: true });
      const alpha = probeTool("probe_alpha", 100);
      const { runtime } = runtimeFixture({
        llmClientsPath: llmClientsPath(),
        profiles: [
          {
            name: "mixer",
            kind: "main",
            llmClientId: CODEX_CLIENT_ID,
            allowed: ["probe_alpha", "probe_beta"],
            maxTurns: 8,
            body: [
              PARALLEL_BODY,
              "When asked for multiple tool calls in one assistant turn, call every named tool in that same response; do not skip a named tool to avoid an expected error.",
            ].join(" "),
          },
        ],
        baseTools: [alpha.definition, beta.definition],
      });
      const run = runtime.startRun({
        agentName: "mixer",
        initialMessages: [
          userMessage(
            [
              "1. In one single assistant turn, call probe_alpha and probe_beta together - two tool calls in the same response. Both will fail with a policy error; that is expected.",
              "2. Then call probe_beta alone, with no other tool call in that turn.",
              '3. Then call submit_main_outcome with summary "solo ok".',
            ].join("\n"),
          ),
        ],
      });
      const outcome = await run.handle.outcome;
      expect(outcome.status).toBe("completed");
      expect(asString(submissionOf(outcome).summary)).toContain("solo ok");

      const rejected = toolResultsIn(outcome.llm).filter(
        (result) => result.is_error && result.content.includes("must be called alone"),
      );
      expect(
        rejected.length,
        "every call of the mixed batch carries the policy rejection",
      ).toBeGreaterThanOrEqual(2);
      expect(
        beta.windows(),
        "the flagged tool dispatched exactly once: solo, never in the batch",
      ).toHaveLength(1);
      expect(
        alpha.windows(),
        "the innocent sibling was never dispatched either",
      ).toHaveLength(0);
    },
  );
});
