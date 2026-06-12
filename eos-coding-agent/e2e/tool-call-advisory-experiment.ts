import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { assistantText, toolUses, type Message } from "@eos/contracts";
import { eosAgentsPath, scriptedTool } from "@eos/testkit";

import { createAgentRuntime } from "../src/runtime.js";
import { runTranscriptPath } from "../src/transcript.js";
import type { RunSummary } from "../src/run-registry.js";
import {
  tempDir,
  userMessage,
  writeProfile,
  type ProfileSpec,
} from "../tests/support.js";
import {
  CODEX_CLIENT_ID,
  loadConfiguredCodexRuntime,
} from "./support/codex-runtime.js";
import { ADVISOR_BODY } from "./support/fixtures.js";

const MAIN_BODY = "You are a test agent.";
const USER_PROMPT = "Write roughly 500 words of original song lyrics.";
const MAX_PRINTED_STRING = 220;
const FAILING_ADVISOR_BODY = [
  "You are the advisor.",
  "Read the caller transcript, then read the final JSON object on the last line of the next message.",
  "Call submit_advisor_outcome exactly once.",
  'Use summary "fail".',
  'Use payload {"verdict":"fail","tool_name":<copied tool_name>,"payload":<copied payload>,"reason":"rejecting for e2e experiment"}.',
  "Do not call any other tool and write no prose.",
].join(" ");

function usage(): never {
  console.error(
    [
      "Usage: pnpm exec tsx packages/agent-runtime/e2e/tool-call-advisory-experiment.ts [--baseline-notifications]",
      "",
      "Default: real hooks, no notification reminders.",
      "--baseline-notifications: use repo production notification rules.",
      "--advisor-fail: make the advisor return a fail verdict instead of pass.",
    ].join("\n"),
  );
  process.exit(2);
}

const unknownArgs = process.argv
  .slice(2)
  .filter(
    (arg) =>
      arg !== "--baseline-notifications" &&
      arg !== "--advisor-fail" &&
      arg !== "--help",
  );
if (process.argv.includes("--help")) usage();
if (unknownArgs.length > 0) {
  console.error(`Unknown argument(s): ${unknownArgs.join(", ")}`);
  usage();
}

const useBaselineNotifications = process.argv.includes("--baseline-notifications");
const advisorShouldFail = process.argv.includes("--advisor-fail");
const codex = loadConfiguredCodexRuntime();
if (!codex.available) {
  console.error(`Codex runtime unavailable: ${codex.reason}`);
  process.exit(1);
}
const llmClientsPath = codex.llmClientsPath;

const maxTurns = Number.parseInt(process.env.EOS_EXPERIMENT_MAX_TURNS ?? "5", 10);
const timeoutMs = Number.parseInt(
  process.env.EOS_EXPERIMENT_TIMEOUT_MS ?? "180000",
  10,
);

function printMessage(message: Message, index: number): void {
  console.log(`#${index.toString()} ${message.role}`);
  const text = assistantText(message).trim();
  if (text.length > 0) {
    console.log(compactString(text));
  }
  for (const use of toolUses(message)) {
    console.log(`tool_use ${use.name} ${JSON.stringify(redactLongStrings(use.input))}`);
  }
  for (const block of message.content) {
    if (block.type === "tool_result") {
      console.log(
        `tool_result ${block.tool_use_id} error=${String(block.is_error)} ${compactString(block.content)}`,
      );
    }
  }
}

function toolUseOrder(llm: readonly Message[]): string[] {
  return llm.flatMap((message) => toolUses(message).map((use) => use.name));
}

async function waitForFinishedRows(listRuns: () => readonly RunSummary[]): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (listRuns().every((row) => row.status === "finished")) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
}

function compactString(value: string): string {
  if (value.length <= MAX_PRINTED_STRING) return value;
  return `${value.slice(0, MAX_PRINTED_STRING)}... <truncated ${String(value.length - MAX_PRINTED_STRING)} chars>`;
}

function redactLongStrings(value: unknown): unknown {
  if (typeof value === "string") return compactString(value);
  if (Array.isArray(value)) return value.map(redactLongStrings);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, redactLongStrings(item)]),
    );
  }
  return value;
}

function redactTranscript(raw: string): string {
  return raw
    .trim()
    .split("\n")
    .filter((line) => line.length > 0)
    .map((line) => {
      try {
        return JSON.stringify(redactLongStrings(JSON.parse(line)));
      } catch {
        return compactString(line);
      }
    })
    .join("\n");
}

async function main(): Promise<void> {
  const root = tempDir("eos-tool-call-advisory-experiment-");
  const profilesDir = join(root, "profiles");
  mkdirSync(profilesDir, { recursive: true });
  writeProfile(profilesDir, {
    name: "one_step",
    kind: "main",
    llmClientId: CODEX_CLIENT_ID,
    allowed: ["tool_call", "ask_advisor"],
    maxTurns,
    body: MAIN_BODY,
  });
  writeProfile(profilesDir, {
    name: "advisor",
    kind: "advisor",
    llmClientId: CODEX_CLIENT_ID,
    allowed: [],
    maxTurns: 3,
    body: advisorShouldFail ? FAILING_ADVISOR_BODY : ADVISOR_BODY,
  });

  const dataDir = join(root, "data");
  const runtime = createAgentRuntime({
    agentProfilesDir: profilesDir,
    llmClientsPath,
    baseTools: [
      scriptedTool({
        name: "tool_call",
        description: "Run tool_call. Takes no arguments and returns { step }.",
        execute: () => Promise.resolve({ content: { step: "tool_call" } }),
      }),
    ],
    hookConfigPath: eosAgentsPath("hooks.json"),
    notificationRulesPath: useBaselineNotifications
      ? eosAgentsPath("notification_rules.json")
      : eosAgentsPath("tests/notification-rules/none.json"),
    dataDir,
  });

  console.log(`root=${root}`);
  console.log(`main_system_prompt=${JSON.stringify(MAIN_BODY)}`);
  console.log(`user_prompt=${JSON.stringify(USER_PROMPT)}`);
  console.log(`max_turns=${maxTurns.toString()}`);
  console.log(`timeout_ms=${timeoutMs.toString()}`);
  console.log(
    `notification_rules=${useBaselineNotifications ? "baseline" : "none"}`,
  );
  console.log(`advisor_mode=${advisorShouldFail ? "fail" : "pass"}`);

  const abort = new AbortController();
  const timer = setTimeout(() => {
    abort.abort("experiment timeout");
  }, timeoutMs);
  const run = runtime.startRun({
    agentName: "one_step",
    initialMessages: [userMessage(USER_PROMPT)],
    signal: abort.signal,
  });

  const outcome = await run.handle.outcome.finally(() => {
    clearTimeout(timer);
  });
  const order = toolUseOrder(outcome.llm);
  const advisorIndex = order.indexOf("ask_advisor");
  const submitIndex = order.indexOf("submit_main_outcome");

  console.log("\n=== result ===");
  console.log(`status=${outcome.status}`);
  console.log(`turns=${outcome.turns.toString()}`);
  console.log(`tool_order=${order.join(" -> ") || "(none)"}`);
  console.log(
    `advisor_before_submit=${String(
      advisorIndex >= 0 && submitIndex >= 0 && advisorIndex < submitIndex,
    )}`,
  );
  if (outcome.status === "completed") {
    console.log(`submission=${JSON.stringify(redactLongStrings(outcome.submission))}`);
  } else if (outcome.status === "failed") {
    console.log(`failure=${JSON.stringify(outcome.failure)}`);
  } else {
    console.log(`cancelled=${outcome.reason}`);
  }

  console.log("\n=== provider history ===");
  outcome.llm.forEach(printMessage);

  await waitForFinishedRows(() => runtime.listRuns());

  console.log("\n=== transcripts ===");
  for (const row of runtime.listRuns()) {
    const path = runTranscriptPath(dataDir, row.run_id);
    console.log(`--- ${row.agent_name} ${row.run_id} status=${row.status} ---`);
    console.log(redactTranscript(readFileSync(path, "utf8")));
  }
}

void main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.stack : String(error));
  process.exit(1);
});
