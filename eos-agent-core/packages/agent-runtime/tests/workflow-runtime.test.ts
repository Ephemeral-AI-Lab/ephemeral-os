import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

import type { JsonObject } from "@eos/contracts";
import type { LlmClient } from "@eos/llm-client";

import { createAgentRuntime, type AgentRuntime } from "../src/runtime.js";
import { runTranscriptPath } from "../src/transcript.js";
import {
  MockLlmClient,
  assistantMessage,
  complete,
  hangingTurn,
  lastToolResultJson,
  llmRegistry,
  must,
  readResultLines,
  readTranscriptLines,
  scriptedTurn,
  tempDir,
  textBlock,
  toolUseBlock,
  userMessage,
  writeProfile,
  type ScriptedTurn,
} from "./support.js";

// --- the §10 reference scripts, verbatim shapes, as fixtures ---------------------

const VARIABLE_REFERENCE_MAP = `
function create_variable_reference_map(ctx) {
  const workflow = ctx.workflow_context.workflow;
  const current_iteration = workflow.iterations.find(
    (i) => i.id === ctx.current.iteration_id,
  ) ?? null;
  const all_attempts = current_iteration ? current_iteration.attempts : [];
  const current_attempt = all_attempts.find((a) => a.id === ctx.current.attempt_id) ?? null;
  const previous_attempt =
    all_attempts
      .filter((a) => current_attempt && a.sequence < current_attempt.sequence)
      .at(-1) ?? null;
  const all_work_items = workflow.iterations.flatMap((iteration) =>
    iteration.attempts.flatMap((attempt) => attempt.work_items),
  );
  const current_work_item =
    "work_item_id" in ctx.current
      ? all_work_items.find((item) => item.id === ctx.current.work_item_id) ?? null
      : null;
  const dependencies = current_work_item
    ? current_work_item.needs.map(
        (id) => all_work_items.find((item) => item.id === id) ?? { id },
      )
    : [];
  const attempt_outcome = (attempt) =>
    attempt === null
      ? null
      : {
          attempt_id: attempt.id,
          status: attempt.status,
          fail_reason: attempt.fail_reason,
          plan_summary: attempt.plan.summary,
          work_items: attempt.work_items.map((item) => ({
            id: item.id,
            status: item.status,
            summary: item.summary,
            outcome: item.outcome,
          })),
        };
  const goal_for_iteration = (iteration_id) => {
    const index = workflow.iterations.findIndex((iteration) => iteration.id === iteration_id);
    let goal = workflow.goal;
    for (let cursor = 1; cursor <= index; cursor += 1) {
      goal = workflow.iterations[cursor - 1].deferred_goal ?? goal;
    }
    return goal;
  };
  return {
    kind: ctx.kind,
    workflow_goal: goal_for_iteration(ctx.current.iteration_id),
    current_iteration_focus: current_iteration ? current_iteration.focus : null,
    previous_attempt_outcome: attempt_outcome(previous_attempt),
    work_item_description: current_work_item ? current_work_item.description : null,
    work_item_spec: current_work_item ? current_work_item.spec : null,
    dependency_outcomes: dependencies.map((item) => ({
      id: item.id,
      description: item.description ?? null,
      status: item.status ?? "Unknown",
      summary: item.summary ?? null,
      outcome: item.outcome ?? null,
    })),
  };
}

module.exports = { create_variable_reference_map };
`;

const PLANNER_SCRIPT = `
const { create_variable_reference_map } = require("./variable_reference_map.cjs");

function get_initial_messages(vars) {
  const user = (text) => ({ role: "user", content: [{ type: "text", text }] });
  const messages = [user("# Workflow goal\\n" + vars.workflow_goal)];
  if (vars.current_iteration_focus === null) {
    messages.push(user("Declare this iteration's focus and work items."));
  } else {
    messages.push(user("# Iteration focus\\n" + vars.current_iteration_focus));
    if (vars.previous_attempt_outcome !== null) {
      messages.push(user("# Previous attempt\\n" + JSON.stringify(vars.previous_attempt_outcome)));
    }
    messages.push(user("Submit planner outcome with work items for this focus."));
  }
  return messages;
}

let input = "";
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", () => {
  const ctx = JSON.parse(input);
  const vars = create_variable_reference_map(ctx);
  const initial_messages = get_initial_messages(vars);
  process.stdout.write(JSON.stringify({ initial_messages }));
});
`;

const WORKER_SCRIPT = `
const { create_variable_reference_map } = require("./variable_reference_map.cjs");

function get_initial_messages(vars) {
  const user = (text) => ({ role: "user", content: [{ type: "text", text }] });
  const messages = [user("# Workflow goal\\n" + vars.workflow_goal)];
  messages.push(user("# Iteration focus\\n" + (vars.current_iteration_focus ?? "")));
  messages.push(user("# Work item description\\n" + (vars.work_item_description ?? "")));
  messages.push(user("# Work item\\n" + (vars.work_item_spec ?? "")));
  if (vars.dependency_outcomes.length > 0) {
    messages.push(user("# Dependencies\\n" + JSON.stringify(vars.dependency_outcomes)));
  }
  messages.push(user("Submit worker outcome for this work item."));
  return messages;
}

let input = "";
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", () => {
  const ctx = JSON.parse(input);
  const vars = create_variable_reference_map(ctx);
  const initial_messages = get_initial_messages(vars);
  process.stdout.write(JSON.stringify({ initial_messages }));
});
`;

interface WorkflowFixtureOptions {
  clients: Record<string, LlmClient>;
  plannerScript?: string;
}

function workflowRuntimeFixture(options: WorkflowFixtureOptions): {
  runtime: AgentRuntime;
  dataDir: string;
  contextRoot: string;
} {
  const root = tempDir("eos-wf-runtime-");
  const profilesDir = join(root, "profiles");
  mkdirSync(profilesDir, { recursive: true });
  const scriptsDir = join(root, "scripts");
  mkdirSync(scriptsDir, { recursive: true });
  writeFileSync(join(scriptsDir, "variable_reference_map.cjs"), VARIABLE_REFERENCE_MAP);
  writeFileSync(join(scriptsDir, "planner.cjs"), options.plannerScript ?? PLANNER_SCRIPT);
  writeFileSync(join(scriptsDir, "worker.cjs"), WORKER_SCRIPT);

  writeProfile(profilesDir, {
    name: "orchestrator",
    kind: "main",
    llmClientId: "main_llm",
    allowed: [
      "ask_advisor",
      "delegate_workflow",
      "list_background_sessions",
      "cancel_background_session",
    ],
  });
  writeProfile(profilesDir, {
    name: "planner",
    kind: "planner",
    llmClientId: "planner_llm",
    allowed: ["ask_advisor"],
    workflowContextScript: join(scriptsDir, "planner.cjs"),
  });
  writeProfile(profilesDir, {
    name: "worker",
    kind: "worker",
    llmClientId: "worker_llm",
    allowed: ["ask_advisor"],
    workflowContextScript: join(scriptsDir, "worker.cjs"),
  });

  const dataDir = join(root, "data");
  const contextRoot = join(root, "workflow-context");
  const runtime = createAgentRuntime({
    agentProfilesDir: profilesDir,
    llmClients: llmRegistry(options.clients),
    hookConfigPath: join(root, "hooks.json"),
    notificationRulesPath: join(root, "notification_rules.json"),
    dataDir,
    workflowDb: ":memory:",
    workflowContextRoot: contextRoot,
    workflowScriptsDir: scriptsDir,
  });
  return { runtime, dataDir, contextRoot };
}

function delegateTurn(goal: string): ScriptedTurn {
  return scriptedTurn([
    complete(
      assistantMessage(toolUseBlock("tu_d", "delegate_workflow", { goal })),
      "tool_use",
    ),
  ]);
}

const submitMainTurn = scriptedTurn([
  complete(
    assistantMessage(toolUseBlock("tu_m", "submit_main_outcome", { summary: "done" })),
    "tool_use",
  ),
]);

function plannerSubmissionTurn(workItems: JsonObject[]): ScriptedTurn {
  return scriptedTurn([
    complete(
      assistantMessage(
        toolUseBlock("tu_p", "submit_planner_outcome", {
          summary: "planned both items",
          iteration_focus: "the whole goal",
          work_items: workItems,
        }),
      ),
      "tool_use",
    ),
  ]);
}

function workerSubmissionTurn(id: string, summary: string): ScriptedTurn {
  return scriptedTurn([
    complete(
      assistantMessage(
        toolUseBlock(`tu_w_${id}`, "submit_worker_outcome", {
          summary,
          is_pass: true,
          outcome: `${summary} in detail`,
        }),
      ),
      "tool_use",
    ),
  ]);
}

describe("workflow runtime end-to-end (§16 case 12)", () => {
  it("delegates, runs scripted planner and workers through real engine loops, auto-waits, and submits", async () => {
    const mainClient = new MockLlmClient([
      delegateTurn("build the thing"),
      scriptedTurn([complete(assistantMessage(textBlock("waiting")))]),
      submitMainTurn,
    ]);
    const plannerClient = new MockLlmClient([
      plannerSubmissionTurn([
        {
          id: "a",
          agent_name: "worker",
          description: "first item",
          work_item_spec: "do the first item",
          needs: [],
        },
        {
          id: "b",
          agent_name: "worker",
          description: "second item",
          work_item_spec: "do the second item",
          needs: ["a"],
        },
      ]),
    ]);
    const workerClient = new MockLlmClient([
      workerSubmissionTurn("a", "first item shipped"),
      workerSubmissionTurn("b", "second item shipped"),
    ]);
    const { runtime } = workflowRuntimeFixture({
      clients: {
        main_llm: mainClient,
        planner_llm: plannerClient,
        worker_llm: workerClient,
      },
    });

    const run = runtime.startRun({
      agentName: "orchestrator",
      initialMessages: [userMessage("orchestrate the thing")],
    });
    const outcome = await run.handle.outcome;
    expect(outcome.status).toBe("completed");

    // The planner's initial messages are EXACTLY the script's output -
    // nothing merged around them (§2.12).
    const plannerRequest = must(plannerClient.requests.at(0));
    expect(plannerRequest.messages).toEqual([
      userMessage("# Workflow goal\nbuild the thing"),
      userMessage("Declare this iteration's focus and work items."),
    ]);

    // Worker A: description + spec from the snapshot, no dependencies.
    const workerARequest = must(workerClient.requests.at(0));
    expect(workerARequest.messages).toEqual([
      userMessage("# Workflow goal\nbuild the thing"),
      userMessage("# Iteration focus\nthe whole goal"),
      userMessage("# Work item description\nfirst item"),
      userMessage("# Work item\ndo the first item"),
      userMessage("Submit worker outcome for this work item."),
    ]);

    // Worker B sees its dependency outcomes, fully expanded by the script.
    const workerBRequest = must(workerClient.requests.at(1));
    const texts = workerBRequest.messages.flatMap((message) =>
      message.content.flatMap((block) => (block.type === "text" ? [block.text] : [])),
    );
    expect(texts).toContain("# Work item description\nsecond item");
    expect(
      texts.some(
        (text) =>
          text.startsWith("# Dependencies\n") &&
          text.includes("first item shipped"),
      ),
      "dependency outcomes ride the worker's initial messages",
    ).toBe(true);

    // The settlement notification reached the caller's conversation.
    const settled = mainClient.requests
      .flatMap((request) => request.messages)
      .flatMap((message) => message.content)
      .filter((block) => block.type === "text")
      .map((block) => block.text)
      .find((text) => text.includes("session_settled"));
    expect(settled, "session_settled drained into the caller").toBeDefined();
    expect(settled).toContain('"workflow"');
    expect(settled).toContain('"completed"');
    expect(settled).toContain("planned both items");
  });

  it("cancel_background_session mid-workflow cascades workflow_cancelled into child transcripts", async () => {
    let plannerStarted!: () => void;
    const started = new Promise<void>((resolve) => (plannerStarted = resolve));
    // The second turn gates on the planner having started, then cancels
    // the workflow session by the id the delegate result returned.
    const gatedCancelTurn: ScriptedTurn = async function* (request) {
      await started;
      const result = lastToolResultJson(request);
      yield complete(
        assistantMessage(
          toolUseBlock("tu_c", "cancel_background_session", {
            type: "workflow",
            id: result.workflow_id,
            reason: "wrong direction",
          }),
        ),
        "tool_use",
      );
    };
    const mainClient = new MockLlmClient([
      delegateTurn("never finishes"),
      gatedCancelTurn,
      submitMainTurn,
    ]);
    const plannerClient = new MockLlmClient([hangingTurn(plannerStarted)]);
    const { runtime, dataDir } = workflowRuntimeFixture({
      clients: {
        main_llm: mainClient,
        planner_llm: plannerClient,
        worker_llm: new MockLlmClient([]),
      },
    });

    const run = runtime.startRun({
      agentName: "orchestrator",
      initialMessages: [userMessage("orchestrate, then cancel")],
    });
    const outcome = await run.handle.outcome;
    expect(outcome.status).toBe("completed");

    const plannerRun = must(
      runtime.listRuns().find((entry) => entry.agent_name === "planner"),
    );
    const transcriptPath = runTranscriptPath(dataDir, plannerRun.run_id);
    const finished = readTranscriptLines(transcriptPath).find(
      (line) => line.kind === "run_finished",
    );
    expect(finished).toMatchObject({
      outcome_status: "cancelled",
      interrupt_reason: "workflow_cancelled",
    });
    const result = readResultLines(join(dirname(transcriptPath), "result.jsonl"));
    expect(result.at(0)).toMatchObject({ interrupt_reason: "workflow_cancelled" });
  });

  it("a broken context script drives the case-9 synthesis path live and the session settles failed", async () => {
    const mainClient = new MockLlmClient([
      delegateTurn("doomed goal"),
      scriptedTurn([complete(assistantMessage(textBlock("waiting")))]),
      submitMainTurn,
    ]);
    const { runtime } = workflowRuntimeFixture({
      clients: {
        main_llm: mainClient,
        planner_llm: new MockLlmClient([]),
        worker_llm: new MockLlmClient([]),
      },
      plannerScript: "process.exit(1);\n",
    });

    const run = runtime.startRun({
      agentName: "orchestrator",
      initialMessages: [userMessage("orchestrate the doomed thing")],
    });
    const outcome = await run.handle.outcome;
    expect(outcome.status).toBe("completed");

    const settled = mainClient.requests
      .flatMap((request) => request.messages)
      .flatMap((message) => message.content)
      .filter((block) => block.type === "text")
      .map((block) => block.text)
      .find((text) => text.includes("session_settled"));
    expect(settled).toBeDefined();
    expect(settled).toContain('"failed"');
    expect(settled).toContain("context_script_error");
  });
});

describe("workflow runtime startup validation (§16 case 12)", () => {
  function startupFixture(mutate: {
    plannerScriptPath?: (scriptsDir: string) => string;
    skipPlannerProfile?: boolean;
    secondPlanner?: boolean;
    workflowDb?: string;
    allowDelegateWithoutDb?: boolean;
  }): () => void {
    const root = tempDir("eos-wf-startup-");
    const profilesDir = join(root, "profiles");
    mkdirSync(profilesDir, { recursive: true });
    const scriptsDir = join(root, "scripts");
    mkdirSync(scriptsDir, { recursive: true });
    writeFileSync(join(scriptsDir, "planner.cjs"), PLANNER_SCRIPT);
    writeFileSync(
      join(scriptsDir, "variable_reference_map.cjs"),
      VARIABLE_REFERENCE_MAP,
    );

    writeProfile(profilesDir, {
      name: "orchestrator",
      kind: "main",
      llmClientId: "main_llm",
      allowed: mutate.allowDelegateWithoutDb ? ["delegate_workflow"] : [],
    });
    if (!mutate.skipPlannerProfile) {
      writeProfile(profilesDir, {
        name: "planner",
        kind: "planner",
        llmClientId: "planner_llm",
        allowed: ["ask_advisor"],
        workflowContextScript:
          mutate.plannerScriptPath?.(scriptsDir) ?? join(scriptsDir, "planner.cjs"),
      });
    }
    if (mutate.secondPlanner) {
      writeProfile(profilesDir, {
        name: "planner-b",
        kind: "planner",
        llmClientId: "planner_llm",
        allowed: ["ask_advisor"],
        workflowContextScript: join(scriptsDir, "planner.cjs"),
      });
    }
    return () =>
      createAgentRuntime({
        agentProfilesDir: profilesDir,
        llmClients: llmRegistry({
          main_llm: new MockLlmClient([]),
          planner_llm: new MockLlmClient([]),
        }),
        hookConfigPath: join(root, "hooks.json"),
        notificationRulesPath: join(root, "notification_rules.json"),
        dataDir: join(root, "data"),
        workflowScriptsDir: scriptsDir,
        ...(mutate.workflowDb !== undefined && { workflowDb: mutate.workflowDb }),
      });
  }

  it("fails startup on a missing profile script path", () => {
    expect(
      startupFixture({
        plannerScriptPath: (dir) => join(dir, "absent.cjs"),
        workflowDb: ":memory:",
      }),
    ).toThrow(/is not readable/);
  });

  it("fails startup on a script path escaping the script root", () => {
    expect(
      startupFixture({
        plannerScriptPath: (dir) => join(dir, "..", "outside.cjs"),
        workflowDb: ":memory:",
      }),
    ).toThrow(/escapes the script root/);
  });

  it("fails startup on a non-script extension", () => {
    expect(
      startupFixture({
        plannerScriptPath: (dir) => {
          const path = join(dir, "planner.js");
          writeFileSync(path, "x");
          return path;
        },
        workflowDb: ":memory:",
      }),
    ).toThrow(/must be a \.cjs or \.mjs file/);
  });

  it("requires exactly one planner profile when workflowDb is configured", () => {
    expect(
      startupFixture({ secondPlanner: true, workflowDb: ":memory:" }),
    ).toThrow(/exactly one planner profile; found 2/);
    expect(
      startupFixture({ skipPlannerProfile: true, workflowDb: ":memory:" }),
    ).toThrow(/exactly one planner profile; found 0/);
  });

  it("rejects a profile listing delegate_workflow when no workflowDb is configured", () => {
    expect(startupFixture({ allowDelegateWithoutDb: true })).toThrow(
      /allows "delegate_workflow", which is not a known non-terminal tool/,
    );
    expect(
      startupFixture({ allowDelegateWithoutDb: true, workflowDb: ":memory:" }),
    ).not.toThrow();
  });
});
