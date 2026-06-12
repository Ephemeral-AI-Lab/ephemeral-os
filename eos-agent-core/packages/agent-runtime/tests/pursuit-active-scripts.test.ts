import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { ContextScriptOutputSchema } from "@eos/contracts";
import { executeJsonCommand } from "@eos/scripts";

const REPO_ROOT = resolve(import.meta.dirname, "../../../..");
const SCRIPT_ROOT = resolve(REPO_ROOT, ".eos-agents/pursuit/scripts");

function plannerInput() {
  return {
    kind: "planner",
    pursuit_context: {
      pursuit: {
        id: "p-1",
        pursuit_goal: "ship it",
        leg_goal_mode: "dynamic",
        predefined_leg_count: null,
        status: "Running",
        context_path: "pursuit_p-1",
        outcome: null,
        legs: [
          {
            id: "leg-1",
            sequence: 1,
            origin: "initial",
            status: "Running",
            leg_goal: "ship parser",
            leg_goal_version: 1,
            leg_goal_provenance: "inherited from pursuit goal",
            is_leg_goal_mutatable: true,
            next_leg_goal: "ship printer",
            max_attempts: 2,
            context_path: "pursuit_p-1/leg_leg-1",
            outcome: null,
            attempts: [
              {
                id: "attempt-1",
                sequence: 1,
                status: "Running",
                failure_reasons: [],
                is_consistent_with_leg_goal: true,
                context_path: "pursuit_p-1/leg_leg-1/attempt_attempt-1",
                outcome: null,
                leg_goal_version: 1,
                plan: {
                  id: "plan-1",
                  status: "Running",
                  declared_leg_goal: null,
                  declared_next_leg_goal: null,
                  summary: null,
                  agent_run_id: null,
                  leg_goal_version: 1,
                },
                work_items: [] as Record<string, unknown>[],
              },
            ],
          },
        ],
      },
    },
    current: {
      pursuit_id: "p-1",
      leg_id: "leg-1",
      attempt_id: "attempt-1",
      plan_id: "plan-1",
    },
  };
}

function workerInput() {
  const input = plannerInput();
  const leg = input.pursuit_context.pursuit.legs[0];
  leg.attempts[0].work_items = [
    {
      id: "work-1",
      agent_name: "worker",
      title: "Parser",
      spec: "Implement parser",
      depends_on: [],
      status: "Running",
      summary: null,
      outcome: null,
      agent_run_id: null,
      context_path: `${leg.attempts[0].context_path}/work_item_work-1`,
      leg_goal_version: 1,
    },
  ];
  return {
    kind: "worker",
    pursuit_context: input.pursuit_context,
    current: {
      pursuit_id: "p-1",
      leg_id: "leg-1",
      attempt_id: "attempt-1",
      work_item_id: "work-1",
    },
  };
}

async function runScript(name: string, input: unknown): Promise<string> {
  const result = await executeJsonCommand(
    { command: `"${process.execPath}" "${resolve(SCRIPT_ROOT, name)}"` },
    input,
  );
  expect(result.kind).toBe("exited");
  if (result.kind !== "exited") return "";
  expect(result.code).toBe(0);
  const output = ContextScriptOutputSchema.parse(JSON.parse(result.stdout));
  return output.initial_messages
    .flatMap((message) => message.content)
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("\n");
}

describe("active pursuit context scripts", () => {
  it("renders dynamic planner standing-successor guidance", async () => {
    const text = await runScript("planner.cjs", plannerInput());

    expect(text).toContain("# Standing next_leg_goal\nship printer");
    expect(text).toContain("Omitting both preserves any standing next_leg_goal");
    expect(text).toContain("Success means the full effective leg_goal is achieved");
  });

  it("keeps workers inside their assigned work item and leg goal", async () => {
    const text = await runScript("worker.cjs", workerInput());

    expect(text).toContain("Stay inside the current leg_goal and this work item");
    expect(text).toContain("Do not plan new legs, change leg_goal, or decide next_leg_goal");
  });
});
