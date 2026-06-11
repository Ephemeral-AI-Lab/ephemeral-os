import { describe, expect, it } from "vitest";

import {
  harness,
  plannerPayload,
  until,
  workerPayload,
} from "./support.js";

describe("db guards and cancel (§16 case 10)", () => {
  it("accepts at most one mutation per entity transition under competing submissions", async () => {
    const h = harness();
    const wf = await h.delegate();
    await h.launches[0].submitPlanner(plannerPayload());
    const worker = h.launches[1];

    const first = await worker.submitWorker(
      workerPayload({ summary: "first wins", outcome: "first outcome" }),
    );
    const second = await worker.submitWorker(
      workerPayload({ is_pass: false, summary: "second loses", outcome: "ignored" }),
    );
    expect(first.ok).toBe(true);
    expect(second.ok, "the duplicate no-ops through terminal guards").toBe(true);

    const item = (await h.tree(wf.workflowId)).iterations[0].attempts[0].workItems[0];
    expect(item.status).toBe("Success");
    expect(item.summary, "the losing mutation never landed").toBe("first wins");
    expect(item.outcome).toBe("first outcome");
  });

  it("skips a stale claimed launch after a competing cancel", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const h = harness({
      compose: async (agentName, input) => {
        await gate;
        const { defaultComposeLaunchContext } = await import(
          "../src/context-engine/composer.js"
        );
        return defaultComposeLaunchContext(agentName, input);
      },
    });

    const delegated = h.delegate();
    // The claim committed; the launcher is parked on the composer. Cancel
    // now, then release: the guarded launcher must skip the stale claim.
    await until(async () => {
      const rows = await h.db
        .selectFrom("launch_queue")
        .select("state")
        .execute();
      return rows.some((row) => row.state === "claimed");
    }, "the plan claim committed");

    const workflowRow = await h.db
      .selectFrom("workflows")
      .select("id")
      .executeTakeFirstOrThrow();
    await h.service.cancel(workflowRow.id, "changed my mind");
    release();
    await delegated;

    expect(h.launches, "the stale launch was skipped").toHaveLength(0);
    const tree = await h.tree(workflowRow.id);
    expect(tree.workflow.status).toBe("Cancelled");
  });

  it("cancel cascades top-down in one transaction and resolves the terminal Cancelled", async () => {
    const h = harness();
    const wf = await h.delegate();
    await h.launches[0].submitPlanner(
      plannerPayload({
        work_items: [
          { id: "a", agent_name: "worker", description: "a", work_item_spec: "a", needs: [] },
          { id: "b", agent_name: "worker", description: "b", work_item_spec: "b", needs: ["a"] },
        ],
      }),
    );
    const workerA = h.launches[1];
    expect(workerA.options?.signal?.aborted).toBe(false);

    await h.service.cancel(wf.workflowId, "user asked");
    await expect(wf.terminal).resolves.toEqual({
      status: "Cancelled",
      summary: "user asked",
    });
    expect(workerA.options?.signal?.aborted, "children observe the workflow signal").toBe(
      true,
    );
    expect(workerA.options?.signal?.reason).toBe("workflow_cancelled");

    const tree = await h.tree(wf.workflowId);
    expect(tree.workflow.status).toBe("Cancelled");
    expect(tree.iterations[0].status).toBe("Cancelled");
    const attempt = tree.iterations[0].attempts[0];
    expect(attempt.status).toBe("Cancelled");
    expect(attempt.plan.status, "the submitted plan keeps its terminal Success").toBe(
      "Success",
    );
    expect(attempt.workItems.map((item) => item.status)).toEqual([
      "Cancelled",
      "Cancelled",
    ]);

    // A late natural settlement after the cancel is a no-op.
    const before = JSON.stringify(tree);
    workerA.settle({ status: "completed" });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(JSON.stringify(await h.tree(wf.workflowId))).toBe(before);
  });

  it("a second cancel and a cancel against a closed workflow are no-ops", async () => {
    const h = harness();
    const wf = await h.delegate();
    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(workerPayload());
    await expect(wf.terminal).resolves.toMatchObject({ status: "Success" });

    await h.service.cancel(wf.workflowId, "too late");
    const tree = await h.tree(wf.workflowId);
    expect(tree.workflow.status, "terminal guards hold").toBe("Success");
    await h.service.cancel(wf.workflowId, "still too late");
    expect((await h.tree(wf.workflowId)).workflow.status).toBe("Success");
  });

  it("a submission landing after cancel finds terminal entities and no-ops", async () => {
    const h = harness();
    const wf = await h.delegate();
    await h.launches[0].submitPlanner(plannerPayload());
    const worker = h.launches[1];
    await h.service.cancel(wf.workflowId, "stop");

    const result = await worker.submitWorker(workerPayload());
    expect(result.ok, "the doomed run may terminate; its mutation no-ops").toBe(true);
    const item = (await h.tree(wf.workflowId)).iterations[0].attempts[0].workItems[0];
    expect(item.status).toBe("Cancelled");
    expect(item.summary).toBeNull();
  });
});
