import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { terminalToolDefinitions } from "@eos/tool";

import { createAgentRuntime } from "../src/runtime.js";
import {
  MockLlmClient,
  assistantMessage,
  complete,
  llmRegistry,
  must,
  scriptedTurn,
  tempDir,
  toolUseBlock,
  userMessage,
  writeProfile,
  type ProfileSpec,
} from "../tests/support.js";
import {
  TERSE_BODY,
  requireAdvisoryPassHookEntries,
  submissionOf,
  toolResultsIn,
} from "./support/fixtures.js";

function runtimeFixture(options: {
  profiles: readonly ProfileSpec[];
  clients: Record<string, MockLlmClient>;
}): ReturnType<typeof createAgentRuntime> {
  const root = tempDir("eos-advisory-pass-e2e-");
  const profilesDir = join(root, "profiles");
  mkdirSync(profilesDir, { recursive: true });
  for (const profile of options.profiles) writeProfile(profilesDir, profile);
  const hookConfigPath = join(root, "hooks.json");
  writeFileSync(hookConfigPath, JSON.stringify(requireAdvisoryPassHookEntries()));
  return createAgentRuntime({
    agentProfilesDir: profilesDir,
    llmClients: llmRegistry(options.clients),
    hookConfigPath,
    dataDir: join(root, "data"),
  });
}

describe("advisory pass prehook (e2e)", () => {
  it("denies an advisory-required submission until ask_advisor returns an exact pass", async () => {
    const advisorClient = new MockLlmClient([
      scriptedTurn([
        complete(
          assistantMessage(
            toolUseBlock("tu_advisor_submit", "submit_advisor_outcome", {
              summary: "pass",
              payload: {
                verdict: "pass",
                tool_name: "submit_worker_outcome",
                payload: { summary: "done" },
                reason: "exact payload matches",
              },
            }),
          ),
          "tool_use",
        ),
      ]),
    ]);
    const workerClient = new MockLlmClient([
      scriptedTurn([
        complete(
          assistantMessage(
            toolUseBlock("tu_submit_without_pass", "submit_worker_outcome", {
              summary: "done",
            }),
          ),
          "tool_use",
        ),
      ]),
      scriptedTurn([
        complete(
          assistantMessage(
            toolUseBlock("tu_ask", "ask_advisor", {
              tool_name: "submit_worker_outcome",
              payload: { summary: "done" },
            }),
          ),
          "tool_use",
        ),
      ]),
      scriptedTurn([
        complete(
          assistantMessage(
            toolUseBlock("tu_submit_after_pass", "submit_worker_outcome", {
              summary: "done",
            }),
          ),
          "tool_use",
        ),
      ]),
    ]);

    const runtime = runtimeFixture({
      profiles: [
        {
          name: "worker",
          kind: "worker",
          llmClientId: "worker_llm",
          allowed: ["ask_advisor"],
          maxTurns: 6,
          body: TERSE_BODY,
        },
        {
          name: "advisor",
          kind: "advisor",
          llmClientId: "advisor_llm",
          allowed: [],
          maxTurns: 3,
          body: TERSE_BODY,
        },
      ],
      clients: { worker_llm: workerClient, advisor_llm: advisorClient },
    });

    const run = runtime.startRun({
      agentName: "worker",
      initialMessages: [userMessage("finish with advisory approval")],
    });
    const outcome = await run.handle.outcome;

    expect(outcome.status).toBe("completed");
    expect(submissionOf(outcome)).toEqual({ summary: "done" });

    const results = toolResultsIn(outcome.llm);
    const denied = must(
      results.find((result) => result.tool_use_id === "tu_submit_without_pass"),
    );
    expect(denied.is_error, "the prehook rejects before terminal execute").toBe(true);
    expect(denied.content).toContain("advisory pass required");
    expect(denied.content).toContain("no matching ask_advisor");

    const ask = must(results.find((result) => result.tool_use_id === "tu_ask"));
    expect(ask.is_error, "ask_advisor returns the advisor submission").toBe(false);
    expect(ask.content).toContain('"verdict":"pass"');

    const allowed = must(
      results.find((result) => result.tool_use_id === "tu_submit_after_pass"),
    );
    expect(allowed.is_error, "the exact pass unlocks the final submission").toBe(false);

    const workerSubmission = must(
      terminalToolDefinitions().find(
        (definition) => definition.name === "submit_worker_outcome",
      ),
    );
    const advisorPrompt = must(workerSubmission.advisorPrompt);
    const advisorRequest = must(advisorClient.requests.at(0));
    const [callerTranscript, instruction] = advisorRequest.messages;
    expect(callerTranscript).toBeDefined();
    expect(instruction).toEqual(
      userMessage(
        `${advisorPrompt} Please verify against the below tool name + payload\n{"payload":{"summary":"done"},"tool_name":"submit_worker_outcome"}`,
      ),
    );
  });
});
