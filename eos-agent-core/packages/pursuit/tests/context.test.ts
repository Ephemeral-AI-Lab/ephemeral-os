import { describe, expect, it } from "vitest";

import { listContextSubtree } from "../src/archive/listing.js";
import { buildPursuitContext } from "../src/archive/paths.js";
import { resolveContextPath } from "../src/archive/resolve.js";
import { snapshotPursuitContext } from "../src/context-engine/input.js";
import { readPursuitContext } from "../src/pursuit-context.js";
import {
  allMessageText,
  harness,
  plannerPayload,
  until,
  workerPayload,
  type Harness,
} from "./support.js";

function legacyGoalPath(kind: "current" | "original"): string {
  return `${kind}_goal.md`;
}

async function refocusedPursuit(h: Harness) {
  const wf = await h.delegate("whole goal", 3);
  await h.launches[0].submitPlanner(
    plannerPayload({
      leg_goal: "first direction",
      next_leg_goal: "left for later",
      summary: "planned first direction",
    }),
  );
  await h.launches[1].submitWorker(
    workerPayload({ is_pass: false, summary: "dead end", outcome: "hit a wall" }),
  );
  // The retry planner refocuses: both fields reset, attempt 1 drifts.
  await h.launches[2].submitPlanner(
    plannerPayload({
      leg_goal: "second direction",
      summary: "planned second direction",
    }),
  );
  return wf;
}

describe("loadPursuitTree derived views (§16 case 2)", () => {
  it("tracks the latest declaration and flips consistency on a refocus, with the budget spanning refocuses", async () => {
    const h = harness();
    const wf = await refocusedPursuit(h);
    const tree = await h.tree(wf.pursuitId);
    const leg = tree.legs[0];

    expect(leg.focus).toBe("second direction");
    expect(leg.nextLegGoal, "refocus reset BOTH fields").toBeNull();
    expect(leg.attempts).toHaveLength(2);
    expect(leg.attempts[0].isConsistentWithLegGoal).toBe(false);
    expect(leg.attempts[1].isConsistentWithLegGoal).toBe(true);
    expect(leg.maxAttempts, "budget unchanged by refocus").toBe(3);
    expect(tree.pursuit.activeGoal, "active goal never advances mid-leg").toBe(
      "whole goal",
    );
  });

  it("keep submissions leave the focus view and consistency unchanged", async () => {
    const h = harness();
    const wf = await h.delegate("goal", 3);
    await h.launches[0].submitPlanner(
      plannerPayload({ leg_goal: "the focus", next_leg_goal: "later" }),
    );
    await h.launches[1].submitWorker(workerPayload({ is_pass: false }));
    await h.launches[2].submitPlanner(
      plannerPayload({ leg_goal: undefined, next_leg_goal: undefined }),
    );

    const leg = (await h.tree(wf.pursuitId)).legs[0];
    expect(leg.focus).toBe("the focus");
    expect(leg.nextLegGoal, "keep retains the standing deferral").toBe("later");
    expect(
      leg.attempts.every((attempt) => attempt.isConsistentWithLegGoal),
    ).toBe(true);
  });

  it("derives the goal chain across legs and the snapshot DTO covers the whole tree", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal");
    await h.launches[0].submitPlanner(
      plannerPayload({ leg_goal: "first half", next_leg_goal: "second half" }),
    );
    await h.launches[1].submitWorker(workerPayload());
    const tree = await h.tree(wf.pursuitId);

    expect(tree.legs[0].goal).toBe("whole goal");
    expect(tree.legs[1].goal).toBe("second half");

    const snapshot = snapshotPursuitContext(tree);
    expect(snapshot.pursuit.goal).toBe("whole goal");
    expect(legacyGoalPath("current").slice(0, -3) in snapshot.pursuit).toBe(false);
    expect(legacyGoalPath("original").slice(0, -3) in snapshot.pursuit).toBe(false);
    expect(snapshot.pursuit.legs).toHaveLength(2);
    expect(snapshot.pursuit.legs[0].attempts[0].work_items).toHaveLength(1);
    expect(snapshot.pursuit.legs[0].attempts[0].context_path).toContain(
      `pursuit_${wf.pursuitId}/leg_`,
    );

    const plan = snapshot.pursuit.legs[0].attempts[0].plan;
    expect(plan.status).toBe("Success");
    expect(plan.declared_leg_goal).toBe("first half");
    expect(plan.declared_next_leg_goal).toBe("second half");
    expect(plan.summary).toBe("planned the slice");
    expect(plan.agent_run_id).not.toBeNull();
    expect(
      "context_path" in plan,
      "plans no longer carry a rendered context path",
    ).toBe(false);
  });
});

describe("context path universe (§16 case 3)", () => {
  it("renders one field per file, verbatim, with absent fields as absent paths", async () => {
    const h = harness();
    const wf = await h.delegate("the goal");
    await h.launches[0].submitPlanner(plannerPayload());
    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);

    expect(context.files.get("goal.md")?.content).toBe("the goal");
    expect(context.files.has(legacyGoalPath("current"))).toBe(false);
    expect(context.files.has(legacyGoalPath("original"))).toBe(false);
    expect(context.files.has("outcome.md"), "no outcome before terminal").toBe(false);

    const leg = tree.legs[0];
    const attempt = leg.attempts[0];
    const item = attempt.workItems[0];
    const itemDir = `leg_${leg.id}/attempt_${attempt.id}/work_item_${item.id}`;
    expect(context.files.get(`${itemDir}/description.md`)?.content).toBe(
      "implement the slice",
    );
    expect(context.files.get(`${itemDir}/spec.md`)?.content).toBe(
      "write the code for the slice",
    );
    expect(context.files.has(`${itemDir}/summary.md`), "absent until submitted").toBe(
      false,
    );

    const read = readPursuitContext(context, { path: `${itemDir}/description.md` });
    expect(read.kind).toBe("page");
    if (read.kind === "page") {
      expect(read.page.content, "verbatim, no stamp or status line").toBe(
        "implement the slice",
      );
      expect(read.page.status, "status rides the DTO").toBe("Running");
    }
  });

  it("pages by byte offset over the latest render", async () => {
    const h = harness();
    const wf = await h.delegate("0123456789");
    const context = buildPursuitContext(await h.tree(wf.pursuitId));
    const first = readPursuitContext(context, {
      path: "goal.md",
      maxBytes: 4,
    });
    expect(first.kind).toBe("page");
    if (first.kind !== "page") return;
    expect(first.page.content).toBe("0123");
    expect(first.page.total_bytes).toBe(10);
    expect(first.page.next_offset).toBe(4);

    const rest = readPursuitContext(context, {
      path: "goal.md",
      offset: 4,
    });
    if (rest.kind !== "page") throw new Error("expected a page");
    expect(rest.page.content).toBe("456789");
    expect(rest.page.next_offset).toBeUndefined();
  });

  it("renders directory listings with status and summary first lines", async () => {
    const h = harness();
    const wf = await h.delegate();
    await h.launches[0].submitPlanner(
      plannerPayload({ summary: "first line of plan\nsecond line" }),
    );
    const context = buildPursuitContext(await h.tree(wf.pursuitId));
    const read = readPursuitContext(context, {});
    expect(read.kind).toBe("listing");
    if (read.kind !== "listing") return;

    const byPath = new Map(read.rows.map((row) => [row.path, row]));
    expect(byPath.get("goal.md")?.status).toBe("Running");
    const attemptRow = read.rows.find((row) => /attempt_[^/]+$/.test(row.path));
    expect(
      attemptRow?.summary,
      "the attempt row carries the planner summary first line",
    ).toBe("first line of plan");
  });

  it("derives the leg outcome from the closing attempt without archiving the leg goal on promotion", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal");
    await h.launches[0].submitPlanner(
      plannerPayload({
        leg_goal: "first half",
        next_leg_goal: "second half",
        summary: "the plan summary",
      }),
    );
    await h.launches[1].submitWorker(
      workerPayload({ summary: "worker summary", outcome: "worker outcome" }),
    );
    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);
    const first = tree.legs[0];
    const closing = first.attempts.at(-1);
    if (!closing) throw new Error("expected a closing attempt");

    const outcome = context.files.get(`leg_${first.id}/outcome.md`);
    expect(outcome?.content, "leg outcome = closing attempt outcome").toBe(
      context.files.get(`leg_${first.id}/attempt_${closing.id}/outcome.md`)
        ?.content,
    );
    expect(outcome?.content).toContain("worker summary");
    expect(
      outcome?.content,
      "the planner summary stays an attempt-owned fact",
    ).not.toContain("the plan summary");
    expect(
      outcome?.content,
      "work-item outcome content stays a work-item fact",
    ).not.toContain("worker outcome");

    expect(
      context.directories.has(`superseded/leg_${first.id}`),
      "closed legs are not copied under a root archive",
    ).toBe(false);
    expect(
      context.files.has(`superseded/leg_${first.id}/goal.md`),
      "the leg goal has no superseded field",
    ).toBe(false);
    expect(context.files.get("goal.md")?.content, "root pursuit goal").toBe(
      "whole goal",
    );
  });

  it("pursuit Success keeps goal.md live and adds outcome.md", async () => {
    const h = harness();
    const wf = await h.delegate("only goal");
    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(workerPayload());
    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);
    expect(context.files.get("goal.md")?.content).toBe("only goal");
    expect(context.files.has("superseded"), "nothing superseded without a successor").toBe(
      false,
    );
    const outcome = context.files.get("outcome.md")?.content;
    expect(outcome).toContain(`## leg_${tree.legs[0].id} [Success]`);
    expect(outcome, "leg outcomes carry worker summaries").toContain(
      "did the work",
    );
    expect(outcome, "planner summaries stay out of outcomes").not.toContain(
      "planned the slice",
    );
  });
});

describe("derived outcome files (Phase 05.2 §5-§6)", () => {
  it("flattens the plan: planner summary at attempt_<id>/plan_summary.md, no plan entity anywhere", async () => {
    const h = harness();
    const wf = await h.delegate("the goal");
    await h.launches[0].submitPlanner(plannerPayload({ summary: "planned the slice" }));
    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);
    const leg = tree.legs[0];
    const attemptDir = `leg_${leg.id}/attempt_${leg.attempts[0].id}`;

    expect(context.files.get(`${attemptDir}/plan_summary.md`)?.content).toBe(
      "planned the slice",
    );
    expect(
      [...context.directories.keys()].filter((path) =>
        path.split("/").some((segment) => segment.startsWith("plan_")),
      ),
      "no plan_<id>/ directory exists",
    ).toEqual([]);

    const read = readPursuitContext(context, {});
    if (read.kind !== "listing") throw new Error("expected a listing");
    const planSegments = read.rows.flatMap((row) =>
      row.path.split("/").filter((segment) => segment.startsWith("plan_")),
    );
    expect(
      planSegments.every((segment) => segment === "plan_summary.md"),
      "no plan-owned listing rows",
    ).toBe(true);
  });

  it("creates the attempt outcome only at close, listing work-item summaries in planner order (T2/T3)", async () => {
    const h = harness();
    const wf = await h.delegate("two items");
    await h.launches[0].submitPlanner(
      plannerPayload({
        work_items: [
          {
            id: "w1",
            agent_name: "worker",
            description: "first item",
            work_item_spec: "do the first",
            needs: [],
          },
          {
            id: "w2",
            agent_name: "worker",
            description: "second item",
            work_item_spec: "do the second",
            needs: [],
          },
        ],
      }),
    );
    const workerFor = (description: string) => {
      const launch = h.launches.find((candidate) =>
        allMessageText(candidate.messages).includes(description),
      );
      if (!launch) throw new Error(`no worker launch saw "${description}"`);
      return launch;
    };
    const tree = await h.tree(wf.pursuitId);
    const leg = tree.legs[0];
    const attemptDir = `leg_${leg.id}/attempt_${leg.attempts[0].id}`;

    await workerFor("first item").submitWorker(
      workerPayload({ summary: "first summary" }),
    );
    const midway = buildPursuitContext(await h.tree(wf.pursuitId));
    expect(
      midway.files.has(`${attemptDir}/outcome.md`),
      "no attempt outcome before all work items finish",
    ).toBe(false);

    await workerFor("second item").submitWorker(
      workerPayload({ summary: "second summary" }),
    );
    const closed = await h.tree(wf.pursuitId);
    const attempt = closed.legs[0].attempts[0];
    expect(
      attempt.workItems.map((item) => item.description),
      "work items stay in planner order",
    ).toEqual(["first item", "second item"]);
    const context = buildPursuitContext(closed);
    expect(context.files.get(`${attemptDir}/outcome.md`)?.content).toBe(
      [
        "# Attempt outcome",
        `- work_item_${attempt.workItems[0].id} [Success]: first summary`,
        `- work_item_${attempt.workItems[1].id} [Success]: second summary`,
      ].join("\n"),
    );
  });

  it("a failed attempt with budget left gets failure_reasons.md and outcome.md but closes nothing (T4/T5)", async () => {
    const h = harness();
    const wf = await h.delegate("retry goal", 2);
    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "worker failed" }),
    );
    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);
    const leg = tree.legs[0];
    const failed = leg.attempts[0];
    const failedDir = `leg_${leg.id}/attempt_${failed.id}`;

    expect(context.files.get(`${failedDir}/failure_reasons.md`)?.content).toContain(
      "worker failed",
    );
    expect(context.files.get(`${failedDir}/outcome.md`)?.content).toBe(
      `# Attempt outcome\n- work_item_${failed.workItems[0].id} [Failed]: worker failed`,
    );
    expect(leg.attempts, "the retry attempt appears").toHaveLength(2);
    expect(
      context.directories.has(
        `leg_${leg.id}/attempt_${leg.attempts[1].id}`,
      ),
      "the retry attempt directory is live",
    ).toBe(true);
    expect(
      context.files.has(`leg_${leg.id}/outcome.md`),
      "no leg outcome while budget remains",
    ).toBe(false);
    expect(context.files.has("outcome.md"), "no pursuit outcome yet").toBe(false);
  });

  it("exhausting the retry budget creates attempt, leg, and pursuit outcomes (T6)", async () => {
    const h = harness();
    const wf = await h.delegate("doomed goal", 1);
    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "could not do it" }),
    );
    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);
    const leg = tree.legs[0];
    const attempt = leg.attempts[0];
    const attemptOutcome = `# Attempt outcome\n- work_item_${attempt.workItems[0].id} [Failed]: could not do it`;

    expect(tree.pursuit.status).toBe("Failed");
    expect(
      context.files.get(`leg_${leg.id}/attempt_${attempt.id}/outcome.md`)
        ?.content,
    ).toBe(attemptOutcome);
    expect(
      context.files.get(`leg_${leg.id}/outcome.md`)?.content,
      "the failed closing attempt outcome becomes the leg outcome",
    ).toBe(attemptOutcome);
    expect(
      context.files.get("outcome.md")?.content,
      "the pursuit outcome includes the failed leg outcome",
    ).toBe(`# Pursuit outcome\n\n## leg_${leg.id} [Failed]\n${attemptOutcome}`);
  });

  it("a planner death renders (no work items) with no plan_summary.md (T1F)", async () => {
    const h = harness();
    const wf = await h.delegate("goal", 2);
    h.launches[0].settle({ status: "failed" });
    await until(() => h.launches.length === 2, "the retry planner launched");

    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);
    const leg = tree.legs[0];
    const dead = leg.attempts[0];
    const deadDir = `leg_${leg.id}/attempt_${dead.id}`;

    expect(
      context.files.has(`${deadDir}/plan_summary.md`),
      "a dead planner leaves no summary",
    ).toBe(false);
    expect(context.files.get(`${deadDir}/outcome.md`)?.content).toBe(
      "# Attempt outcome\n(no work items)",
    );
    expect(context.files.get(`${deadDir}/failure_reasons.md`)?.content).toContain(
      "without a submission",
    );
  });

  it("a multi-leg success renders every leg outcome in sequence order (T7/T8)", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal");
    await h.launches[0].submitPlanner(
      plannerPayload({ leg_goal: "first half", next_leg_goal: "second half" }),
    );
    await h.launches[1].submitWorker(workerPayload({ summary: "first half done" }));

    const midTree = await h.tree(wf.pursuitId);
    const midway = buildPursuitContext(midTree);
    expect(
      midway.files.has(`leg_${midTree.legs[0].id}/outcome.md`),
      "the promoted-from leg closes with an outcome at T7",
    ).toBe(true);
    expect(midway.files.has("outcome.md"), "the pursuit is still running at T7").toBe(
      false,
    );

    await h.launches[2].submitPlanner(
      plannerPayload({ leg_goal: "second half focus" }),
    );
    await h.launches[3].submitWorker(workerPayload({ summary: "second half done" }));

    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);
    const section = (leg: (typeof tree.legs)[number]): string => {
      const attempt = leg.attempts.at(-1);
      const item = attempt?.workItems[0];
      if (!attempt || !item) throw new Error("expected a closing attempt");
      return `## leg_${leg.id} [Success]\n# Attempt outcome\n- work_item_${item.id} [Success]: ${item.summary ?? ""}`;
    };
    expect(tree.pursuit.status).toBe("Success");
    expect(
      context.files.get("outcome.md")?.content,
      "the pursuit outcome is the ordered leg ledger",
    ).toBe(
      `# Pursuit outcome\n\n${section(tree.legs[0])}\n\n${section(tree.legs[1])}`,
    );
  });

  it("cancellation renders a marker, never business outcomes for cancelled entities (T10)", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal");
    await h.launches[0].submitPlanner(
      plannerPayload({ leg_goal: "first half", next_leg_goal: "second half" }),
    );
    await h.launches[1].submitWorker(workerPayload({ summary: "first half done" }));
    await wf.cancel("changed direction");

    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);
    const [first, second] = tree.legs;

    expect(tree.pursuit.status).toBe("Cancelled");
    expect(second.status).toBe("Cancelled");
    expect(
      context.files.has(`leg_${second.id}/outcome.md`),
      "no business outcome for a cancelled leg",
    ).toBe(false);
    expect(
      context.files.has(
        `leg_${second.id}/attempt_${second.attempts[0].id}/outcome.md`,
      ),
      "no business outcome for a cancelled attempt",
    ).toBe(false);
    const root = context.files.get("outcome.md")?.content ?? "";
    expect(
      root.startsWith("# Pursuit outcome\npursuit cancelled"),
      "the cancellation marker leads",
    ).toBe(true);
    expect(root, "already closed legs keep their outcomes").toContain(
      `## leg_${first.id} [Success]`,
    );
    expect(root).not.toContain(`## leg_${second.id}`);
  });
});

describe("keep vs refocus paths (§16 case 6)", () => {
  it("relocates drifted attempts whole under superseded/ with declaration files on the declarer only", async () => {
    const h = harness();
    const wf = await refocusedPursuit(h);
    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);
    const leg = tree.legs[0];
    const drifted = leg.attempts[0];
    const live = leg.attempts[1];
    const supersededDir = `leg_${leg.id}/superseded/attempt_${drifted.id}`;

    expect(
      context.files.get(`${supersededDir}/leg_goal.md`)?.content,
      "the superseded declaration rides the declaring attempt",
    ).toBe("first direction");
    expect(context.files.get(`${supersededDir}/next_leg_goal.md`)?.content).toBe(
      "left for later",
    );
    expect(context.files.get(`${supersededDir}/failure_reasons.md`)?.content).toContain(
      "dead end",
    );
    expect(
      context.files.get(`${supersededDir}/plan_summary.md`)?.content,
      "the superseded attempt keeps its attempt-owned plan summary",
    ).toBe("planned first direction");
    expect(
      context.files.get(`${supersededDir}/outcome.md`)?.content,
      "the superseded attempt keeps its derived outcome",
    ).toContain("dead end");
    const driftedItem = drifted.workItems[0];
    expect(
      context.files.get(
        `${supersededDir}/work_item_${driftedItem.id}/outcome.md`,
      )?.content,
      "the drifted attempt relocates whole, live shapes included",
    ).toBe("hit a wall");

    expect(
      context.directories.has(`leg_${leg.id}/attempt_${drifted.id}`),
      "the old live path is gone",
    ).toBe(false);
    expect(
      context.directories.has(`leg_${leg.id}/attempt_${live.id}`),
      "the consistent attempt stays live",
    ).toBe(true);
    expect(
      context.files.has(`leg_${leg.id}/attempt_${live.id}/leg_goal.md`),
      "live attempts carry no declaration files",
    ).toBe(false);
    expect(
      context.files.get(
        `leg_${leg.id}/attempt_${live.id}/plan_summary.md`,
      )?.content,
      "the live attempt owns its own plan summary",
    ).toBe("planned second direction");

    expect(context.files.get(`leg_${leg.id}/leg_goal.md`)?.content).toBe(
      "second direction",
    );
    expect(
      context.files.has(`leg_${leg.id}/next_leg_goal.md`),
      "refocus reset the deferral",
    ).toBe(false);
  });

  it("errors a stale live path naming superseded/ among the valid children", async () => {
    const h = harness();
    const wf = await refocusedPursuit(h);
    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);
    const leg = tree.legs[0];
    const drifted = leg.attempts[0];

    const resolved = resolveContextPath(
      context,
      `leg_${leg.id}/attempt_${drifted.id}/failure_reasons.md`,
    );
    expect(resolved.kind).toBe("error");
    if (resolved.kind === "error") {
      expect(resolved.message).toContain("superseded");
      expect(resolved.message).toContain("unknown context path");
    }
  });

  it("the retry directive after failure carries only consistent attempts and omits the standing next_leg_goal", async () => {
    const h = harness();
    await h.delegate("goal", 4);
    await h.launches[0].submitPlanner(
      plannerPayload({
        leg_goal: "direction one",
        next_leg_goal: "the deferral",
      }),
    );
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "failure one" }),
    );
    await h.launches[2].submitPlanner(
      plannerPayload({ leg_goal: "direction two" }),
    );
    await h.launches[3].submitWorker(
      workerPayload({ is_pass: false, summary: "failure two" }),
    );

    const retryText = allMessageText(h.launches[4].messages);
    expect(retryText).toContain("# Leg focus\ndirection two");
    expect(retryText).toContain("failure two");
    expect(retryText, "superseded attempts omitted").not.toContain("failure one");
    expect(retryText, "standing next_leg_goal omitted").not.toContain("the deferral");
  });

  it("collapses prior legs and superseded rows to status rows in listings", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal", 3);
    await h.launches[0].submitPlanner(
      plannerPayload({ leg_goal: "first direction", next_leg_goal: "later" }),
    );
    await h.launches[1].submitWorker(workerPayload({ is_pass: false }));
    // Refocus carrying a fresh deferral, then close to mint a successor.
    await h.launches[2].submitPlanner(
      plannerPayload({ leg_goal: "second direction", next_leg_goal: "phase 2" }),
    );
    await h.launches[3].submitWorker(workerPayload());
    const tree = await h.tree(wf.pursuitId);
    const context = buildPursuitContext(tree);
    const read = readPursuitContext(context, {});
    if (read.kind !== "listing") throw new Error("expected a listing");

    const first = tree.legs[0];
    const priorRows = read.rows.filter((row) =>
      row.path.startsWith(`leg_${first.id}`),
    );
    expect(priorRows, "prior leg collapses to one status row").toHaveLength(1);
    expect(priorRows[0].status).toBe("Success");

    const rootArchiveRows = read.rows.filter((row) => row.path.startsWith("superseded/"));
    expect(rootArchiveRows, "no root archive rows remain").toHaveLength(0);

    const insideSuperseded = readPursuitContext(context, {
      path: `leg_${first.id}/superseded/attempt_${first.attempts[0].id}`,
    });
    expect(insideSuperseded.kind, "superseded files stay readable at full fidelity").toBe(
      "listing",
    );
    if (insideSuperseded.kind === "listing") {
      expect(
        insideSuperseded.rows.some((row) => row.path.endsWith("leg_goal.md")),
      ).toBe(true);
    }

    const listing = listContextSubtree(context, "");
    expect(listing.length).toBe(read.rows.length);
  });
});
