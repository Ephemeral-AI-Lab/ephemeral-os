import { describe, expect, it } from "vitest";

import {
  ContextScriptOutputSchema,
  PlannerContextInputSchema,
  PlannerOutcomePayloadSchema,
  WorkerContextInputSchema,
  WorkerOutcomePayloadSchema,
  WorkflowEntityRunStatusSchema,
  isWorkflowEntityTerminal,
  mintWorkflowId,
  workflowIdFrom,
} from "../src/index.js";

const WORK_ITEM = {
  id: "wi-1",
  agent_name: "worker",
  description: "implement the parser",
  work_item_spec: "write the parser module",
};

function snapshot() {
  return {
    workflow: {
      id: "wf-1",
      goal: "ship it",
      status: "Running",
      context_path: "workflow_wf-1",
      iterations: [],
    },
  };
}

function legacyWorkflowGoalKey(kind: "original" | "current"): string {
  return `${kind}_goal`;
}

describe("workflow ids and status", () => {
  it("mints and adopts branded workflow ids", () => {
    expect(mintWorkflowId()).toMatch(/[0-9a-f-]{36}/);
    expect(workflowIdFrom("wf-1")).toBe("wf-1");
    expect(() => workflowIdFrom("")).toThrow();
  });

  it.each`
    status         | terminal
    ${"NotStarted"} | ${false}
    ${"Running"}    | ${false}
    ${"Success"}    | ${true}
    ${"Failed"}     | ${true}
    ${"Cancelled"}  | ${true}
  `("classifies $status terminal=$terminal", ({ status, terminal }) => {
    const parsed = WorkflowEntityRunStatusSchema.parse(status);
    expect(isWorkflowEntityTerminal(parsed)).toBe(terminal);
  });
});

describe("planner outcome payload", () => {
  it("accepts a declaring payload with work items and defaults needs", () => {
    const parsed = PlannerOutcomePayloadSchema.parse({
      summary: "planned",
      iteration_focus: "the parser slice",
      deferred_goal: "the printer slice",
      work_items: [WORK_ITEM],
    });
    expect(parsed.work_items[0].needs).toEqual([]);
  });

  it("accepts a keep payload that omits the declaration pair", () => {
    const parsed = PlannerOutcomePayloadSchema.parse({
      summary: "re-planned",
      work_items: [{ ...WORK_ITEM, needs: ["wi-0"] }],
    });
    expect(parsed.iteration_focus).toBeUndefined();
    expect(parsed.deferred_goal).toBeUndefined();
  });

  it("rejects deferred_goal without iteration_focus", () => {
    const result = PlannerOutcomePayloadSchema.safeParse({
      summary: "planned",
      deferred_goal: "the printer slice",
      work_items: [WORK_ITEM],
    });
    expect(result.success).toBe(false);
    expect(JSON.stringify(result.error?.issues)).toContain(
      "deferred_goal requires iteration_focus",
    );
  });

  it.each([
    ["empty work_items", { work_items: [] }],
    ["missing description", { work_items: [{ ...WORK_ITEM, description: undefined }] }],
    ["empty summary", { summary: "" }],
    ["empty iteration_focus", { iteration_focus: "" }],
  ] as const)("rejects %s", (_name, override) => {
    const base = {
      summary: "planned",
      iteration_focus: "slice",
      work_items: [WORK_ITEM],
    };
    expect(
      PlannerOutcomePayloadSchema.safeParse({ ...base, ...override }).success,
    ).toBe(false);
  });
});

describe("worker outcome payload", () => {
  it("accepts the documented shape", () => {
    expect(
      WorkerOutcomePayloadSchema.parse({
        summary: "done",
        is_pass: true,
        outcome: "parser written",
      }).is_pass,
    ).toBe(true);
  });

  it("rejects a missing is_pass", () => {
    expect(
      WorkerOutcomePayloadSchema.safeParse({ summary: "done", outcome: "x" }).success,
    ).toBe(false);
  });
});

describe("context-script inputs", () => {
  it("carries only workflow_context plus current for the planner", () => {
    const input = {
      kind: "planner",
      workflow_context: snapshot(),
      current: {
        workflow_id: "wf-1",
        iteration_id: "it-1",
        attempt_id: "at-1",
        plan_id: "pl-1",
      },
    };
    expect(PlannerContextInputSchema.parse(input)).toEqual(input);
    expect(
      PlannerContextInputSchema.safeParse({ ...input, revision: 3 }).success,
      "envelope metadata fields must be rejected",
    ).toBe(false);
    expect(
      PlannerContextInputSchema.safeParse({
        ...input,
        workflow_context: {
          workflow: {
            ...input.workflow_context.workflow,
            [legacyWorkflowGoalKey("original")]: "ship it",
          },
        },
      }).success,
      "legacy workflow goal field rejected",
    ).toBe(false);
    expect(
      PlannerContextInputSchema.safeParse({
        ...input,
        workflow_context: {
          workflow: {
            ...input.workflow_context.workflow,
            [legacyWorkflowGoalKey("current")]: "ship it",
          },
        },
      }).success,
      "derived workflow goal field rejected",
    ).toBe(false);
  });

  it("keeps plan metadata in the snapshot but rejects a plan context_path", () => {
    const plan = {
      id: "pl-1",
      status: "Success",
      declared_focus: "the parser slice",
      declared_deferred_goal: null,
      summary: "planned",
      agent_run_id: "run-1",
    };
    const attempt = {
      id: "at-1",
      sequence: 1,
      status: "Running",
      fail_reason: null,
      is_consistent_with_iteration_focus: true,
      context_path: "workflow_wf-1/iteration_it-1/attempt_at-1",
      plan,
      work_items: [],
    };
    const iteration = {
      id: "it-1",
      sequence: 1,
      origin: "initial",
      status: "Running",
      focus: "the parser slice",
      deferred_goal: null,
      max_attempts: 2,
      context_path: "workflow_wf-1/iteration_it-1",
      attempts: [attempt],
    };
    const input = {
      kind: "planner",
      workflow_context: {
        workflow: { ...snapshot().workflow, iterations: [iteration] },
      },
      current: {
        workflow_id: "wf-1",
        iteration_id: "it-1",
        attempt_id: "at-1",
        plan_id: "pl-1",
      },
    };
    expect(PlannerContextInputSchema.parse(input)).toEqual(input);
    expect(
      PlannerContextInputSchema.safeParse({
        ...input,
        workflow_context: {
          workflow: {
            ...input.workflow_context.workflow,
            iterations: [
              {
                ...iteration,
                attempts: [
                  {
                    ...attempt,
                    plan: { ...plan, context_path: `${attempt.context_path}/plan_pl-1` },
                  },
                ],
              },
            ],
          },
        },
      }).success,
      "plans no longer carry a rendered context path",
    ).toBe(false);
  });

  it("carries only workflow_context plus current for the worker", () => {
    const input = {
      kind: "worker",
      workflow_context: snapshot(),
      current: {
        workflow_id: "wf-1",
        iteration_id: "it-1",
        attempt_id: "at-1",
        work_item_id: "wi-1",
      },
    };
    expect(WorkerContextInputSchema.parse(input)).toEqual(input);
    expect(
      WorkerContextInputSchema.safeParse({
        ...input,
        current: { ...input.current, plan_id: "pl-1" },
      }).success,
      "a worker locator must not carry a plan id",
    ).toBe(false);
  });
});

describe("context-script output", () => {
  it("accepts ordered user messages with real content blocks", () => {
    const parsed = ContextScriptOutputSchema.parse({
      initial_messages: [
        { role: "user", content: [{ type: "text", text: "# Workflow goal\nship" }] },
      ],
    });
    expect(parsed.initial_messages).toHaveLength(1);
  });

  it("rejects empty initial_messages", () => {
    expect(ContextScriptOutputSchema.safeParse({ initial_messages: [] }).success).toBe(
      false,
    );
  });

  it("rejects non-user messages", () => {
    expect(
      ContextScriptOutputSchema.safeParse({
        initial_messages: [{ role: "assistant", content: [{ type: "text", text: "x" }] }],
      }).success,
    ).toBe(false);
  });

  it("rejects string-content shortcuts that are not real content blocks", () => {
    expect(
      ContextScriptOutputSchema.safeParse({
        initial_messages: [{ role: "user", content: "just a string" }],
      }).success,
      "string content",
    ).toBe(false);
    expect(
      ContextScriptOutputSchema.safeParse({
        initial_messages: [{ role: "user", content: ["just a string"] }],
      }).success,
      "string array content",
    ).toBe(false);
  });
});
