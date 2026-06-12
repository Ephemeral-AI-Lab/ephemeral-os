import { describe, expect, it } from "vitest";

import {
  allMessageText,
  harness,
  plannerPayload,
  until,
  workerPayload,
  workItem,
} from "./support.js";

describe("pursuit creation and planner declarations", () => {
  it("creates a dynamic first leg with pursuit_goal as leg_goal and accepts keep payloads", async () => {
    const h = harness();
    const pursuit = await h.create("ship the parser");

    expect(h.launches).toHaveLength(1);
    expect(h.launches[0].agentName).toBe("planner");
    expect(h.launches[0].options?.parent).toBe("parent-run");

    const initial = await h.tree(pursuit.pursuit_id);
    expect(initial.pursuit.pursuitGoal).toBe("ship the parser");
    expect(initial.pursuit.legGoalMode).toBe("dynamic");
    expect(initial.legs[0]).toMatchObject({
      legGoal: "ship the parser",
      legGoalVersion: 1,
      nextLegGoal: null,
      isLegGoalMutatable: true,
    });
    expect(allMessageText(h.launches[0].messages)).toContain(
      "# Current leg goal\nship the parser",
    );

    const accepted = await h.launches[0].submitPlanner(plannerPayload());
    expect(accepted.ok).toBe(true);
    expect(h.launches, "root work item launched").toHaveLength(2);
    const afterPlan = await h.tree(pursuit.pursuit_id);
    expect(afterPlan.legs[0].attempts[0].plan.declaredLegGoal).toBeNull();
    expect(afterPlan.legs[0].attempts[0].workItems[0].title).toBe(
      "implement the leg",
    );
  });

  it("predefined mode rejects planner leg-goal declarations and promotes fixed legs", async () => {
    const h = harness();
    const pursuit = await h.create("ship all", { legGoals: ["parser", "printer"] });

    const initial = await h.tree(pursuit.pursuit_id);
    expect(initial.pursuit.legGoalMode).toBe("predefined");
    expect(initial.legs[0].legGoal).toBe("parser");
    expect(initial.legs[0].nextLegGoal).toBe("printer");

    const rejected = await h.launches[0].submitPlanner(
      plannerPayload({ leg_goal: "new parser" }),
    );
    expect(rejected.ok).toBe(false);
    if (!rejected.ok) expect(rejected.error).toContain("predefined");
    expect(
      (await h.tree(pursuit.pursuit_id)).legs[0].attempts[0].workItems,
      "correctable declaration error does not materialize work",
    ).toHaveLength(0);

    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(workerPayload());

    const promoted = await h.tree(pursuit.pursuit_id);
    expect(promoted.legs).toHaveLength(2);
    expect(promoted.legs[1]).toMatchObject({
      origin: "predefined",
      legGoal: "printer",
      isLegGoalMutatable: false,
    });
  });
});

describe("scheduler dependency and failure behavior", () => {
  it("blocks only not-started dependents and waits for unrelated running work before failing", async () => {
    const h = harness();
    const pursuit = await h.create("ship graph", { maxAttempts: 2 });
    await h.launches[0].submitPlanner(
      plannerPayload({
        work_items: [
          workItem("root"),
          workItem("dependent", ["root"]),
          workItem("unrelated"),
        ],
      }),
    );
    expect(h.launches.map((launch) => launch.agentName)).toEqual([
      "planner",
      "worker",
      "worker",
    ]);

    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "root failed" }),
    );
    const afterRootFailure = await h.tree(pursuit.pursuit_id);
    const attempt = afterRootFailure.legs[0].attempts[0];
    expect(attempt.status, "attempt stays open while unrelated work runs").toBe(
      "Running",
    );
    expect(
      attempt.workItems.map((item) => [String(item.id), item.status]).sort(),
    ).toEqual([
      ["dependent", "Blocked"],
      ["root", "Failed"],
      ["unrelated", "Running"],
    ]);

    await h.launches[2].submitWorker(workerPayload());
    const closed = await h.tree(pursuit.pursuit_id);
    expect(closed.legs[0].attempts[0].status).toBe("Failed");
    expect(closed.legs[0].attempts[0].failureReasons).toEqual([
      "work_item root failed: root failed",
      "work_item dependent blocked by failed dependency",
    ]);
    expect(closed.legs[0].attempts, "retry created after failed close").toHaveLength(2);
    expect(h.launches.at(-1)?.agentName).toBe("planner");
  });

  it("allows retry plans to depend on successful prior-attempt work in the same leg-goal version", async () => {
    const h = harness();
    await h.create("ship retry", { maxAttempts: 2 });
    await h.launches[0].submitPlanner(
      plannerPayload({ work_items: [workItem("base"), workItem("breaker")] }),
    );
    await h.launches[1].submitWorker(workerPayload({ summary: "base done" }));
    await h.launches[2].submitWorker(
      workerPayload({ is_pass: false, summary: "breaker failed" }),
    );

    const retryPlanner = h.launches[3];
    const accepted = await retryPlanner.submitPlanner(
      plannerPayload({ work_items: [workItem("followup", ["base"])] }),
    );
    expect(accepted.ok).toBe(true);
    expect(h.launches.at(-1)?.options?.submission?.kind).toBe("worker");
    expect(allMessageText(h.launches.at(-1)?.messages ?? [])).toContain(
      "base done",
    );
  });

  it("closes failed after compose failures exhaust the attempt budget", async () => {
    const h = harness({ compose: () => Promise.reject(new Error("script exploded")) });
    const pursuit = await h.create("doomed", { maxAttempts: 2 });

    await until(async () => {
      const tree = await h.tree(pursuit.pursuit_id);
      return tree.pursuit.status === "Failed";
    }, "pursuit failed after compose failures");

    const tree = await h.tree(pursuit.pursuit_id);
    expect(tree.legs[0].attempts).toHaveLength(2);
    expect(tree.legs[0].attempts[0].failureReasons).toEqual([
      "context_script_error: script exploded",
    ]);
    await expect(pursuit.settle()).resolves.toMatchObject({ status: "Failed" });
  });
});

describe("dynamic refocus", () => {
  it("increments leg_goal_version, clears omitted next_leg_goal, and supersedes older attempts", async () => {
    const h = harness();
    const pursuit = await h.create("whole goal", { maxAttempts: 3 });
    await h.launches[0].submitPlanner(
      plannerPayload({ next_leg_goal: "later", work_items: [workItem("old")] }),
    );
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "old failed" }),
    );

    const retry = h.launches[2];
    await retry.submitPlanner(
      plannerPayload({ leg_goal: "narrowed goal", work_items: [workItem("new")] }),
    );

    const tree = await h.tree(pursuit.pursuit_id);
    const leg = tree.legs[0];
    expect(leg.legGoal).toBe("narrowed goal");
    expect(leg.legGoalVersion).toBe(2);
    expect(leg.nextLegGoal, "omitted successor cleared during refocus").toBeNull();
    expect(leg.attempts[0].isConsistentWithLegGoal).toBe(false);
    expect(leg.attempts[1].isConsistentWithLegGoal).toBe(true);
  });
});
