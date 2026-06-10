import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { scriptedTool } from "@eos/testkit";

import { loadAgentProfile } from "../src/agent-profile-loader.js";
import {
  loadAgentProfileRegistry,
  selectProfileDefinitions,
  type KnownToolNames,
} from "../src/agent-profile-registry.js";
import { tempDir, writeProfile } from "./support.js";

/** The §4 worker example, verbatim frontmatter shape. */
const WORKER_PROFILE = `---
name: worker
description: Worker
llm_client_id: codex_coding_plan
max_turns: 100
agent_kind: worker
allowed_tools:
  - read
  - multi_read
  - write
  - edit
  - exec_command
  - command_stdin
  - read_command_transcript
  - list_background_sessions
  - cancel_background_session
  - ask_advisor
terminal_tool: submit_worker_outcome
---

You are the worker for one assigned work item.

Before terminal submission, call \`ask_advisor\` with
\`tool_name="submit_worker_outcome"\` and the exact payload you intend to
send.
`;

const SANDBOX_NAMES = [
  "read",
  "multi_read",
  "write",
  "edit",
  "exec_command",
  "command_stdin",
  "read_command_transcript",
] as const;

const KNOWN: KnownToolNames = {
  ordinary: new Set([
    ...SANDBOX_NAMES,
    "list_background_sessions",
    "cancel_background_session",
    "run_subagent",
    "ask_advisor",
    "read_agent_run_transcript",
  ]),
  terminal: new Set([
    "submit_main_outcome",
    "submit_planner_outcome",
    "submit_worker_outcome",
    "submit_advisor_outcome",
    "submit_subagent_outcome",
  ]),
};

function workerDir(): string {
  const dir = join(tempDir("eos-profiles-"), "profiles");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "worker.md"), WORKER_PROFILE);
  return dir;
}

describe("agent profile loader and registry", () => {
  it("loads the worker-format Markdown profile by agent name (§13.1)", () => {
    const dir = workerDir();
    const registry = loadAgentProfileRegistry(dir, KNOWN);
    const profile = registry.require("worker");
    expect(profile).toMatchObject({
      name: "worker",
      description: "Worker",
      llm_client_id: "codex_coding_plan",
      max_turns: 100,
      agent_kind: "worker",
      terminal_tool: "submit_worker_outcome",
      source_path: join(dir, "worker.md"),
    });
    expect(profile.allowed_tools).toEqual([
      ...SANDBOX_NAMES,
      "list_background_sessions",
      "cancel_background_session",
      "ask_advisor",
    ]);
    expect(profile.system_prompt.startsWith("You are the worker")).toBe(true);
    expect(
      profile.system_prompt.includes('tool_name="submit_worker_outcome"'),
      "the body after frontmatter is the system prompt",
    ).toBe(true);
  });

  it("throws on an unknown agent name, naming the known ones", () => {
    const registry = loadAgentProfileRegistry(workerDir(), KNOWN);
    expect(() => registry.require("nobody")).toThrow(
      'unknown agent profile "nobody" (known: worker)',
    );
  });

  it("rejects duplicate profile names across files at startup (§13.1)", () => {
    const dir = workerDir();
    writeFileSync(
      join(dir, "worker-copy.md"),
      WORKER_PROFILE, // same `name: worker` under a second file name
    );
    expect(() => loadAgentProfileRegistry(dir, KNOWN)).toThrow(
      /duplicate agent profile name "worker"/,
    );
  });

  it("rejects a missing profiles directory at startup", () => {
    expect(() =>
      loadAgentProfileRegistry(join(tempDir("eos-none-"), "absent"), KNOWN),
    ).toThrow(/is not readable/);
  });

  it.each`
    breakage                                | mutate                                                          | expected
    ${"missing llm_client_id"}              | ${(raw: string) => raw.replace(/^llm_client_id:.*\n/m, "")}     | ${/llm_client_id/}
    ${"zero max_turns"}                     | ${(raw: string) => raw.replace("max_turns: 100", "max_turns: 0")} | ${/max_turns/}
    ${"non-numeric max_turns"}              | ${(raw: string) => raw.replace("max_turns: 100", "max_turns: many")} | ${/max_turns/}
    ${"unknown allowed_tools entry"}        | ${(raw: string) => raw.replace("  - read\n", "  - teleport\n")} | ${/allows "teleport", which is not a known non-terminal tool/}
    ${"unknown terminal_tool"}              | ${(raw: string) => raw.replace("terminal_tool: submit_worker_outcome", "terminal_tool: submit_nothing")} | ${/selects "submit_nothing", which is not a known terminal tool/}
    ${"non-terminal terminal_tool"}         | ${(raw: string) => raw.replace("terminal_tool: submit_worker_outcome", "terminal_tool: run_subagent")} | ${/selects "run_subagent", which is not a known terminal tool/}
    ${"terminal_tool inside allowed_tools"} | ${(raw: string) => raw.replace("  - ask_advisor\n", "  - ask_advisor\n  - submit_worker_outcome\n")} | ${/lists its terminal_tool "submit_worker_outcome" under allowed_tools/}
    ${"no frontmatter block"}               | ${() => "just prose, no frontmatter\n"}                         | ${/must open with a --- YAML frontmatter block/}
  `(
    "fails at startup on $breakage (§13.1)",
    ({ mutate, expected }: { mutate: (raw: string) => string; expected: RegExp }) => {
      const dir = join(tempDir("eos-profiles-"), "profiles");
      mkdirSync(dir, { recursive: true });
      writeFileSync(join(dir, "worker.md"), mutate(WORKER_PROFILE));
      expect(() => loadAgentProfileRegistry(dir, KNOWN)).toThrow(expected);
    },
  );

  it("parses a profile whose body is empty into an empty system prompt", () => {
    const dir = join(tempDir("eos-profiles-"), "profiles");
    mkdirSync(dir, { recursive: true });
    const path = writeProfile(dir, {
      name: "advisor",
      kind: "advisor",
      llmClientId: "advisor_llm",
      allowed: [],
      body: "",
    });
    expect(loadAgentProfile(path).system_prompt).toBe("");
  });

  it("selects exactly allowed_tools + terminal_tool from the available definitions (§2.8)", () => {
    const registry = loadAgentProfileRegistry(workerDir(), KNOWN);
    const profile = registry.require("worker");
    const define = (name: string): ReturnType<typeof scriptedTool> =>
      scriptedTool({ name, execute: () => Promise.resolve({ content: name }) });
    const available = [
      ...SANDBOX_NAMES.map(define),
      define("list_background_sessions"),
      define("cancel_background_session"),
      define("ask_advisor"),
      define("run_subagent"), // known, but not allowed by this profile
      define("submit_worker_outcome"),
      define("submit_main_outcome"), // terminal inventory entry not selected
    ];
    const selected = selectProfileDefinitions(profile, available).map(
      (definition) => definition.name as string,
    );
    expect(selected).toEqual([
      ...SANDBOX_NAMES,
      "list_background_sessions",
      "cancel_background_session",
      "ask_advisor",
      "submit_worker_outcome",
    ]);
  });
});
