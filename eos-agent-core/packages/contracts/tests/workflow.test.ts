import { describe, expect, it } from "vitest";

import {
  ContextScriptOutputSchema,
  PlannerContextInputSchema,
  PlannerOutcomePayloadSchema,
  WorkerContextInputSchema,
  WorkerOutcomePayloadSchema,
  PursuitEntityRunStatusSchema,
  isPursuitEntityTerminal,
  mintPursuitId,
  pursuitIdFrom,
} from "../src/index.js";

const WORK_ITEM = {
  id: "wi-1",
  agent_name: "worker",
  description: "implement the parser",
  work_item_spec: "write the parser module",
};

function snapshot() {
  return {
    pursuit: {
      id: "wf-1",
      goal: "ship it",
      status: "Running",
      context_path: "pursuit_wf-1",
      legs: [],
    },
  };
}

function legacyPursuitGoalKey(kind: "original" | "current"): string {
  return `${kind}_goal`;
}

describe("pursuit ids and status", () => {
  it("mints and adopts branded pursuit ids", () => {
    expect(mintPursuitId()).toMatch(/[0-9a-f-]{36}/);
    expect(pursuitIdFrom("wf-1")).toBe("wf-1");
    expect(() => pursuitIdFrom("")).toThrow();
  });

  it.each`
    status         | terminal
    ${"NotStarted"} | ${false}
    ${"Running"}    | ${false}
    ${"Success"}    | ${true}
    ${"Failed"}     | ${true}
    ${"Cancelled"}  | ${true}
  `("classifies $status terminal=$terminal", ({ status, terminal }) => {
    const parsed = PursuitEntityRunStatusSchema.parse(status);
    expect(isPursuitEntityTerminal(parsed)).toBe(terminal);
  });
});

describe("planner outcome payload", () => {
  it("accepts a declaring payload with work items and defaults needs", () => {
    const parsed = PlannerOutcomePayloadSchema.parse({
      summary: "planned",
      leg_goal: "the parser slice",
      next_leg_goal: "the printer slice",
      work_items: [WORK_ITEM],
    });
    expect(parsed.work_items[0].needs).toEqual([]);
  });

  it("accepts a keep payload that omits the declaration pair", () => {
    const parsed = PlannerOutcomePayloadSchema.parse({
      summary: "re-planned",
      work_items: [{ ...WORK_ITEM, needs: ["wi-0"] }],
    });
    expect(parsed.leg_goal).toBeUndefined();
    expect(parsed.next_leg_goal).toBeUndefined();
  });

  it("rejects next_leg_goal without leg_goal", () => {
    const result = PlannerOutcomePayloadSchema.safeParse({
      summary: "planned",
      next_leg_goal: "the printer slice",
      work_items: [WORK_ITEM],
    });
    expect(result.success).toBe(false);
    expect(JSON.stringify(result.error?.issues)).toContain(
      "next_leg_goal requires leg_goal",
    );
  });

  it.each([
    ["empty work_items", { work_items: [] }],
    ["missing description", { work_items: [{ ...WORK_ITEM, description: undefined }] }],
    ["empty summary", { summary: "" }],
    ["empty leg_goal", { leg_goal: "" }],
  ] as const)("rejects %s", (_name, override) => {
    const base = {
      summary: "planned",
      leg_goal: "slice",
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
  it("carries only pursuit_context plus current for the planner", () => {
    const input = {
      kind: "planner",
      pursuit_context: snapshot(),
      current: {
        pursuit_id: "wf-1",
        leg_id: "it-1",
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
        pursuit_context: {
          pursuit: {
            ...input.pursuit_context.pursuit,
            [legacyPursuitGoalKey("original")]: "ship it",
          },
        },
      }).success,
      "legacy pursuit goal field rejected",
    ).toBe(false);
    expect(
      PlannerContextInputSchema.safeParse({
        ...input,
        pursuit_context: {
          pursuit: {
            ...input.pursuit_context.pursuit,
            [legacyPursuitGoalKey("current")]: "ship it",
          },
        },
      }).success,
      "derived pursuit goal field rejected",
    ).toBe(false);
  });

  it("keeps plan metadata in the snapshot but rejects a plan context_path", () => {
    const plan = {
      id: "pl-1",
      status: "Success",
      declared_leg_goal: "the parser slice",
      declared_next_leg_goal: null,
      summary: "planned",
      agent_run_id: "run-1",
    };
    const attempt = {
      id: "at-1",
      sequence: 1,
      status: "Running",
      failure_reasons: null,
      is_consistent_with_leg_goal: true,
      context_path: "pursuit_wf-1/leg_it-1/attempt_at-1",
      plan,
      work_items: [],
    };
    const leg = {
      id: "it-1",
      sequence: 1,
      origin: "initial",
      status: "Running",
      focus: "the parser slice",
      next_leg_goal: null,
      max_attempts: 2,
      context_path: "pursuit_wf-1/leg_it-1",
      attempts: [attempt],
    };
    const input = {
      kind: "planner",
      pursuit_context: {
        pursuit: { ...snapshot().pursuit, legs: [leg] },
      },
      current: {
        pursuit_id: "wf-1",
        leg_id: "it-1",
        attempt_id: "at-1",
        plan_id: "pl-1",
      },
    };
    expect(PlannerContextInputSchema.parse(input)).toEqual(input);
    expect(
      PlannerContextInputSchema.safeParse({
        ...input,
        pursuit_context: {
          pursuit: {
            ...input.pursuit_context.pursuit,
            legs: [
              {
                ...leg,
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

  it("carries only pursuit_context plus current for the worker", () => {
    const input = {
      kind: "worker",
      pursuit_context: snapshot(),
      current: {
        pursuit_id: "wf-1",
        leg_id: "it-1",
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
        { role: "user", content: [{ type: "text", text: "# Pursuit goal\nship" }] },
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
