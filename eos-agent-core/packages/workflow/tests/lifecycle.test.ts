import { describe, expect, it } from "vitest";

import {
  allMessageText,
  harness,
  plannerPayload,
  until,
  workerPayload,
} from "./support.js";

describe("delegation (§16 case 4)", () => {
  it("creates workflow/iteration/attempt/plan and launches the planner after commit", async () => {
    const h = harness();
    const wf = await h.delegate("ship the parser");

    expect(h.launches, "exactly one launch").toHaveLength(1);
    const planner = h.launches[0];
    expect(planner.agentName).toBe("planner");
    expect(planner.options?.parent).toBe("parent-run");
    expect(planner.options?.submission?.kind).toBe("planner");

    const tree = await h.tree(wf.workflowId);
    expect(tree.workflow.status).toBe("Running");
    expect(tree.workflow.originalGoal).toBe("ship the parser");
    expect(tree.workflow.currentGoal).toBe("ship the parser");
    expect(tree.iterations).toHaveLength(1);
    expect(tree.iterations[0].status).toBe("Running");
    expect(tree.iterations[0].origin).toBe("initial");
    expect(tree.iterations[0].focus, "no focus until the planner declares").toBeNull();
    const attempt = tree.iterations[0].attempts[0];
    expect(attempt.status).toBe("Running");
    expect(attempt.plan.status).toBe("Running");
    expect(attempt.plan.agentRunId, "agent_run_id stamped at launch").toBe(
      planner.runId,
    );
  });

  it("composes the default initial-planner policy: goal plus declaration directive", async () => {
    const h = harness();
    await h.delegate("ship the parser");
    const text = allMessageText(h.launches[0].messages);
    expect(text).toContain("# Current goal\nship the parser");
    expect(text).toContain("iteration_focus");
    expect(text).toContain("submit_planner_outcome");
  });
});

describe("submission validation (§16 case 5)", () => {
  it("rejects a first payload without iteration_focus, then accepts the corrected resubmission exactly once", async () => {
    const h = harness();
    const wf = await h.delegate();
    const planner = h.launches[0];

    const missingFocus = await planner.submitPlanner(
      plannerPayload({ iteration_focus: undefined }),
    );
    expect(missingFocus.ok).toBe(false);
    if (!missingFocus.ok) expect(missingFocus.error).toContain("iteration_focus");
    expect(
      (await h.tree(wf.workflowId)).iterations[0].attempts[0].workItems,
      "no mutation on a correctable payload",
    ).toHaveLength(0);

    const unknownWorker = await planner.submitPlanner(
      plannerPayload({
        work_items: [
          {
            id: "w1",
            agent_name: "ghost",
            description: "x",
            work_item_spec: "y",
            needs: [],
          },
        ],
      }),
    );
    expect(unknownWorker.ok).toBe(false);
    if (!unknownWorker.ok) expect(unknownWorker.error).toContain("ghost");

    const accepted = await planner.submitPlanner(plannerPayload());
    expect(accepted.ok).toBe(true);
    const tree = await h.tree(wf.workflowId);
    expect(tree.iterations[0].focus).toBe("the first slice");
    expect(
      tree.iterations[0].attempts[0].workItems,
      "the accepted resubmission mutates exactly once",
    ).toHaveLength(1);
    expect(h.launches, "the ready work item launched").toHaveLength(2);
    expect(h.launches[1].agentName).toBe("worker");
    expect(h.launches[1].options?.submission?.kind).toBe("worker");
  });

  it("launches roots immediately and dependents only when their needs succeed", async () => {
    const h = harness();
    const wf = await h.delegate();
    await h.launches[0].submitPlanner(
      plannerPayload({
        work_items: [
          {
            id: "root",
            agent_name: "worker",
            description: "root item",
            work_item_spec: "do the root",
            needs: [],
          },
          {
            id: "dependent",
            agent_name: "worker",
            description: "dependent item",
            work_item_spec: "do the follow-up",
            needs: ["root"],
          },
        ],
      }),
    );
    expect(h.launches, "only the root launched").toHaveLength(2);

    await h.launches[1].submitWorker(workerPayload());
    expect(h.launches, "the dependent launched on the unblocking success").toHaveLength(3);
    const tree = await h.tree(wf.workflowId);
    const items = tree.iterations[0].attempts[0].workItems;
    expect(items.map((item) => item.status).sort()).toEqual(["Running", "Success"]);
  });
});

describe("success cascade (§16 case 7)", () => {
  it("promotes the deferral into the next iteration and the goal advances by derivation", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal");
    await h.launches[0].submitPlanner(
      plannerPayload({ iteration_focus: "first half", deferred_goal: "second half" }),
    );
    await h.launches[1].submitWorker(workerPayload());

    const tree = await h.tree(wf.workflowId);
    expect(tree.iterations, "promotion created the next iteration").toHaveLength(2);
    expect(tree.iterations[0].status).toBe("Success");
    expect(tree.iterations[1].origin).toBe("deferred_goal");
    expect(tree.iterations[1].status).toBe("Running");
    expect(tree.workflow.currentGoal, "current_goal advanced to the deferral").toBe(
      "second half",
    );
    expect(tree.workflow.status).toBe("Running");

    expect(h.launches, "the promoted planner launched").toHaveLength(3);
    expect(h.launches[2].agentName).toBe("planner");
    expect(
      allMessageText(h.launches[2].messages),
      "the next planner sees the promoted deferral as its current goal",
    ).toContain("# Current goal\nsecond half");
  });

  it("closes the workflow Success when no deferral is declared and resolves the terminal", async () => {
    const h = harness();
    const wf = await h.delegate();
    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(workerPayload());

    const tree = await h.tree(wf.workflowId);
    expect(tree.workflow.status).toBe("Success");
    expect(tree.iterations[0].status).toBe("Success");
    await expect(wf.terminal).resolves.toEqual({
      status: "Success",
      summary: "planned the slice",
    });
  });
});

describe("failure and retry (§16 case 8)", () => {
  it("fails the attempt, cancels siblings in the same transaction, advances the abort generation, and retries", async () => {
    const h = harness();
    const wf = await h.delegate();
    await h.launches[0].submitPlanner(
      plannerPayload({
        work_items: [
          {
            id: "a",
            agent_name: "worker",
            description: "item a",
            work_item_spec: "spec a",
            needs: [],
          },
          {
            id: "b",
            agent_name: "worker",
            description: "item b",
            work_item_spec: "spec b",
            needs: [],
          },
        ],
      }),
    );
    expect(h.launches).toHaveLength(3);
    const [, workerA, workerB] = h.launches;
    const firstGeneration = workerB.options?.signal;

    await workerA.submitWorker(
      workerPayload({ is_pass: false, summary: "broke the build" }),
    );

    const tree = await h.tree(wf.workflowId);
    const attempt1 = tree.iterations[0].attempts[0];
    expect(attempt1.status).toBe("Failed");
    expect(attempt1.failReason).toContain("broke the build");
    expect(
      attempt1.workItems.map((item) => item.status).sort(),
      "the failing item failed; its sibling was cancelled in the same transaction",
    ).toEqual(["Cancelled", "Failed"]);
    expect(
      attempt1.workItems.every((item) => item.status !== "Running"),
      "no zombie Running rows",
    ).toBe(true);

    expect(firstGeneration?.aborted, "abort generation advanced").toBe(true);
    expect(firstGeneration?.reason).toBe("attempt_failed");

    expect(tree.iterations[0].attempts, "a retry attempt exists").toHaveLength(2);
    expect(h.launches, "the retry planner launched").toHaveLength(4);
    const retryPlanner = h.launches[3];
    expect(retryPlanner.agentName).toBe("planner");
    expect(
      retryPlanner.options?.signal?.aborted,
      "the retry planner rides the NEXT generation",
    ).toBe(false);

    // The cancelled sibling's late settlement is a no-op.
    workerB.settle({ status: "cancelled" });
    await until(async () => {
      const fresh = await h.tree(wf.workflowId);
      return fresh.iterations[0].attempts[0].workItems.length === 2;
    });
    const fresh = await h.tree(wf.workflowId);
    expect(
      fresh.iterations[0].attempts[0].workItems.map((item) => item.status).sort(),
    ).toEqual(["Cancelled", "Failed"]);
  });

  it("exhausting max_attempts closes iteration and workflow Failed with the fail reason recorded", async () => {
    const h = harness();
    const wf = await h.delegate("goal", 2);
    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "first failure" }),
    );
    await h.launches[2].submitPlanner(plannerPayload({ iteration_focus: undefined }));
    await h.launches[3].submitWorker(
      workerPayload({ is_pass: false, summary: "second failure" }),
    );

    const tree = await h.tree(wf.workflowId);
    expect(tree.iterations[0].status).toBe("Failed");
    expect(tree.workflow.status).toBe("Failed");
    await expect(wf.terminal).resolves.toEqual({
      status: "Failed",
      summary: expect.stringContaining("second failure") as string,
    });
  });

  it("retry context carries the failed attempt expanded and the budget counts all attempts", async () => {
    const h = harness();
    await h.delegate("goal", 3);
    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "broke it", outcome: "details here" }),
    );
    const retryText = allMessageText(h.launches[2].messages);
    expect(retryText).toContain("# Iteration focus\nthe first slice");
    expect(retryText).toContain("Failed attempt 1");
    expect(retryText).toContain("broke it");
    expect(retryText).toContain("details here");
    expect(retryText).toContain("refocus");
  });
});

describe("death and compose synthesis (§16 case 9)", () => {
  it("a run that settles without ever submitting synthesizes a failed submission and retries", async () => {
    const h = harness();
    const wf = await h.delegate();
    h.launches[0].settle({ status: "failed" });

    await until(() => h.launches.length === 2, "the retry planner launched");
    const tree = await h.tree(wf.workflowId);
    expect(tree.iterations[0].attempts[0].status).toBe("Failed");
    expect(tree.iterations[0].attempts[0].failReason).toContain(
      "run settled 'failed' without a submission",
    );
    expect(tree.iterations[0].attempts[0].plan.status).toBe("Failed");
    expect(h.launches, "the retry planner launched").toHaveLength(2);
  });

  it("a settlement after an in-run submission is a no-op against the terminal entity", async () => {
    const h = harness();
    const wf = await h.delegate();
    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(workerPayload());
    h.launches[0].settle({ status: "completed" });
    h.launches[1].settle({ status: "completed" });

    await new Promise((resolve) => setTimeout(resolve, 5));
    const tree = await h.tree(wf.workflowId);
    expect(tree.workflow.status).toBe("Success");
    expect(tree.iterations[0].attempts, "no synthesized retry").toHaveLength(1);
  });

  it("a composer failure synthesizes context_script_error and the ordinary retry path bounds it", async () => {
    const h = harness({
      compose: () => Promise.reject(new Error("script exploded")),
    });
    const wf = await h.delegate("goal", 2);

    await until(async () => {
      const tree = await h.tree(wf.workflowId);
      return tree.workflow.status === "Failed";
    }, "workflow failed after compose failures exhausted the budget");
    const tree = await h.tree(wf.workflowId);
    expect(h.launches, "the launch never happens on compose failure").toHaveLength(0);
    expect(tree.iterations[0].attempts).toHaveLength(2);
    for (const attempt of tree.iterations[0].attempts) {
      expect(attempt.status).toBe("Failed");
      expect(attempt.failReason).toContain("context_script_error: script exploded");
    }
    await expect(wf.terminal).resolves.toMatchObject({ status: "Failed" });

    const statuses = tree.iterations[0].attempts.flatMap((attempt) => [
      attempt.status,
      attempt.plan.status,
    ]);
    expect(
      statuses.every((status) => status !== "Running" && status !== "NotStarted"),
      "no entity stays Running",
    ).toBe(true);
  });
});
