import { describe, expect, it } from "vitest";

import {
  toolUseIdFrom,
  workflowIdFrom,
  type DelegatedWorkflow,
  type PlannerOutcomePayload,
  type SubmissionResult,
  type WorkflowTerminal,
} from "@eos/contracts";
import { BackgroundSessionSupervisor } from "@eos/background";
import { NotificationInbox } from "@eos/notification";
import { scriptedRunState } from "@eos/testkit";

import { ToolNameSchema, type ToolCallContext } from "../src/contract.js";
import { snapshotRunState } from "../src/run-state.js";
import { cancelBackgroundSessionTool } from "../src/tools/background/cancel-background-session.js";
import {
  plannerStructureError,
  submitPlannerOutcomeTool,
  submitWorkerOutcomeTool,
} from "../src/tools/submission/index.js";
import { workflowTools } from "../src/tools/workflow/delegate-workflow.js";
import { live, tick } from "./support.js";

const ctx = (): ToolCallContext => ({
  meta: Object.freeze({
    tool_use_id: toolUseIdFrom("tu_wf"),
    tool_name: ToolNameSchema.parse("test_caller"),
    run: snapshotRunState(scriptedRunState()),
  }),
  signal: live(),
});

function scriptedDelegated(id: string): {
  workflow: DelegatedWorkflow;
  resolveTerminal: (terminal: WorkflowTerminal) => void;
  cancelled: string[];
} {
  let resolveTerminal!: (terminal: WorkflowTerminal) => void;
  const cancelled: string[] = [];
  const workflow: DelegatedWorkflow = {
    workflowId: workflowIdFrom(id),
    terminal: new Promise((resolve) => {
      resolveTerminal = resolve;
    }),
    cancel: async (reason) => {
      cancelled.push(reason);
      resolveTerminal({ status: "Cancelled", summary: reason });
      await Promise.resolve();
    },
    describe: () => "the goal",
  };
  return { workflow, resolveTerminal, cancelled };
}

function workItem(id: string, needs: string[] = []) {
  return {
    id,
    agent_name: "worker",
    description: `item ${id}`,
    work_item_spec: `spec ${id}`,
    needs,
  };
}

describe("delegate_workflow (§16 case 11)", () => {
  it("registers the session before returning and reports the workflow id", async () => {
    const inbox = new NotificationInbox();
    const supervisor = new BackgroundSessionSupervisor(inbox);
    const scripted = scriptedDelegated("wf-1");
    const [tool] = workflowTools(async () => {
      expect(
        supervisor.listBackgroundSessions(),
        "registration happens after delegate resolves, before the result",
      ).toHaveLength(0);
      return Promise.resolve(scripted.workflow);
    }, supervisor);

    const outcome = await tool.execute({ goal: "ship it" }, ctx());
    expect(outcome.isError ?? false).toBe(false);
    expect(outcome.content).toEqual({ workflow_id: "wf-1" });
    const sessions = supervisor.listBackgroundSessions();
    expect(sessions).toHaveLength(1);
    expect(sessions[0]).toMatchObject({
      type: "workflow",
      id: "wf-1",
      status: "running",
      description: "the goal",
    });
  });

  it("rejects a second delegation while a workflow session is open", async () => {
    const inbox = new NotificationInbox();
    const supervisor = new BackgroundSessionSupervisor(inbox);
    const first = scriptedDelegated("wf-1");
    const [tool] = workflowTools(() => Promise.resolve(first.workflow), supervisor);

    await tool.execute({ goal: "first" }, ctx());
    const second = await tool.execute({ goal: "second" }, ctx());
    expect(second.isError).toBe(true);
    expect(second.content).toContain("already open");

    // Settled but undelivered still counts as open.
    first.resolveTerminal({ status: "Success", summary: "done" });
    await tick();
    const third = await tool.execute({ goal: "third" }, ctx());
    expect(third.isError, "undelivered settlement still guards").toBe(true);

    inbox.drain();
    const fourth = await tool.execute({ goal: "fourth" }, ctx());
    expect(fourth.isError ?? false, "after delivery the guard lifts").toBe(false);
  });

  it("maps the workflow terminal onto the session outcome", async () => {
    const inbox = new NotificationInbox();
    const supervisor = new BackgroundSessionSupervisor(inbox);
    const scripted = scriptedDelegated("wf-1");
    const [tool] = workflowTools(() => Promise.resolve(scripted.workflow), supervisor);
    await tool.execute({ goal: "ship it" }, ctx());

    scripted.resolveTerminal({ status: "Failed", summary: "budget exhausted" });
    await tick();
    expect(supervisor.listBackgroundSessions()[0]).toMatchObject({
      status: "failed",
      summary: "budget exhausted",
    });
  });

  it("cancel_background_session accepts type workflow and awaits the handle cascade", async () => {
    const inbox = new NotificationInbox();
    const supervisor = new BackgroundSessionSupervisor(inbox);
    const scripted = scriptedDelegated("wf-1");
    const [tool] = workflowTools(() => Promise.resolve(scripted.workflow), supervisor);
    await tool.execute({ goal: "ship it" }, ctx());

    const cancel = cancelBackgroundSessionTool(supervisor);
    const outcome = await cancel.execute(
      { type: "workflow", id: "wf-1", reason: "wrong direction" },
      ctx(),
    );
    expect(outcome.isError ?? false).toBe(false);
    expect(scripted.cancelled, "the handle cancel ran the cascade").toEqual([
      "wrong direction",
    ]);
    expect(
      cancel.input.safeParse({ type: "sandbox", id: "x" }).success,
      "the type union is closed",
    ).toBe(false);
  });
});

describe("per-kind submission validation (§16 case 11)", () => {
  it.each([
    [
      "duplicate local ids",
      [workItem("a"), workItem("a")],
      'duplicate work item id "a"',
    ],
    [
      "dangling needs",
      [workItem("a", ["ghost"])],
      'needs undeclared id "ghost"',
    ],
    [
      "dependency cycles",
      [workItem("a", ["b"]), workItem("b", ["a"])],
      "dependency cycle",
    ],
    ["self cycles", [workItem("a", ["a"])], "dependency cycle"],
  ])("rejects %s in-run", async (_name, workItems, expected) => {
    const payload: PlannerOutcomePayload = {
      summary: "plan",
      iteration_focus: "slice",
      work_items: workItems,
    };
    expect(plannerStructureError(payload)).toContain(expected);

    const submitted: PlannerOutcomePayload[] = [];
    const tool = submitPlannerOutcomeTool({
      kind: "planner",
      submit: (accepted) => {
        submitted.push(accepted);
        return Promise.resolve<SubmissionResult>({ ok: true });
      },
    });
    const outcome = await tool.execute(payload, ctx());
    expect(outcome.isError).toBe(true);
    expect(outcome.content).toContain(expected);
    expect(submitted, "structure errors never reach the binding").toHaveLength(0);
  });

  it("a bound planner submission awaits the binding and surfaces its error for in-run correction", async () => {
    const results: SubmissionResult[] = [
      { ok: false, error: "first declaration must declare iteration_focus" },
      { ok: true },
    ];
    const tool = submitPlannerOutcomeTool({
      kind: "planner",
      submit: () => Promise.resolve(results.shift() ?? { ok: true }),
    });
    const payload: PlannerOutcomePayload = {
      summary: "plan",
      work_items: [workItem("a")],
    };

    const rejected = await tool.execute(payload, ctx());
    expect(rejected.isError).toBe(true);
    expect(rejected.content).toContain("iteration_focus");

    const accepted = await tool.execute(payload, ctx());
    expect(accepted.isError ?? false).toBe(false);
    expect(accepted.content).toEqual({ summary: "plan" });
  });

  it("a bound worker submission maps ok and error results", async () => {
    const okTool = submitWorkerOutcomeTool({
      kind: "worker",
      submit: () => Promise.resolve({ ok: true }),
    });
    const ok = await okTool.execute(
      { summary: "done", is_pass: true, outcome: "all good" },
      ctx(),
    );
    expect(ok.isError ?? false).toBe(false);
    expect(ok.content).toEqual({ summary: "done" });

    const errTool = submitWorkerOutcomeTool({
      kind: "worker",
      submit: () => Promise.resolve({ ok: false, error: "nope" }),
    });
    const err = await errTool.execute(
      { summary: "done", is_pass: true, outcome: "all good" },
      ctx(),
    );
    expect(err.isError).toBe(true);
    expect(err.content).toBe("nope");
  });

  it("unbound planner and worker runs keep service-free submissions", async () => {
    const planner = await submitPlannerOutcomeTool().execute(
      { summary: "plan", iteration_focus: "slice", work_items: [workItem("a")] },
      ctx(),
    );
    expect(planner.isError ?? false).toBe(false);
    expect(planner.content).toMatchObject({ summary: "plan" });

    const worker = await submitWorkerOutcomeTool().execute(
      { summary: "did it", is_pass: false, outcome: "details" },
      ctx(),
    );
    expect(worker.isError ?? false).toBe(false);
    expect(worker.content).toEqual({
      summary: "did it",
      is_pass: false,
      outcome: "details",
    });
  });
});
