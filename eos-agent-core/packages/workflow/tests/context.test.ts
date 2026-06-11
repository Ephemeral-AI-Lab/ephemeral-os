import { describe, expect, it } from "vitest";

import { listContextSubtree } from "../src/archive/listing.js";
import { buildWorkflowContext } from "../src/archive/paths.js";
import { resolveContextPath } from "../src/archive/resolve.js";
import { snapshotWorkflowContext } from "../src/context-engine/input.js";
import { readWorkflowContext } from "../src/workflow-context.js";
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

async function refocusedWorkflow(h: Harness) {
  const wf = await h.delegate("whole goal", 3);
  await h.launches[0].submitPlanner(
    plannerPayload({
      iteration_focus: "first direction",
      deferred_goal: "left for later",
      summary: "planned first direction",
    }),
  );
  await h.launches[1].submitWorker(
    workerPayload({ is_pass: false, summary: "dead end", outcome: "hit a wall" }),
  );
  // The retry planner refocuses: both fields reset, attempt 1 drifts.
  await h.launches[2].submitPlanner(
    plannerPayload({
      iteration_focus: "second direction",
      summary: "planned second direction",
    }),
  );
  return wf;
}

describe("loadWorkflowTree derived views (§16 case 2)", () => {
  it("tracks the latest declaration and flips consistency on a refocus, with the budget spanning refocuses", async () => {
    const h = harness();
    const wf = await refocusedWorkflow(h);
    const tree = await h.tree(wf.workflowId);
    const iteration = tree.iterations[0];

    expect(iteration.focus).toBe("second direction");
    expect(iteration.deferredGoal, "refocus reset BOTH fields").toBeNull();
    expect(iteration.attempts).toHaveLength(2);
    expect(iteration.attempts[0].isConsistentWithIterationFocus).toBe(false);
    expect(iteration.attempts[1].isConsistentWithIterationFocus).toBe(true);
    expect(iteration.maxAttempts, "budget unchanged by refocus").toBe(3);
    expect(tree.workflow.activeGoal, "active goal never advances mid-iteration").toBe(
      "whole goal",
    );
  });

  it("keep submissions leave the focus view and consistency unchanged", async () => {
    const h = harness();
    const wf = await h.delegate("goal", 3);
    await h.launches[0].submitPlanner(
      plannerPayload({ iteration_focus: "the focus", deferred_goal: "later" }),
    );
    await h.launches[1].submitWorker(workerPayload({ is_pass: false }));
    await h.launches[2].submitPlanner(
      plannerPayload({ iteration_focus: undefined, deferred_goal: undefined }),
    );

    const iteration = (await h.tree(wf.workflowId)).iterations[0];
    expect(iteration.focus).toBe("the focus");
    expect(iteration.deferredGoal, "keep retains the standing deferral").toBe("later");
    expect(
      iteration.attempts.every((attempt) => attempt.isConsistentWithIterationFocus),
    ).toBe(true);
  });

  it("derives the goal chain across iterations and the snapshot DTO covers the whole tree", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal");
    await h.launches[0].submitPlanner(
      plannerPayload({ iteration_focus: "first half", deferred_goal: "second half" }),
    );
    await h.launches[1].submitWorker(workerPayload());
    const tree = await h.tree(wf.workflowId);

    expect(tree.iterations[0].goal).toBe("whole goal");
    expect(tree.iterations[1].goal).toBe("second half");

    const snapshot = snapshotWorkflowContext(tree);
    expect(snapshot.workflow.goal).toBe("whole goal");
    expect(legacyGoalPath("current").slice(0, -3) in snapshot.workflow).toBe(false);
    expect(legacyGoalPath("original").slice(0, -3) in snapshot.workflow).toBe(false);
    expect(snapshot.workflow.iterations).toHaveLength(2);
    expect(snapshot.workflow.iterations[0].attempts[0].work_items).toHaveLength(1);
    expect(snapshot.workflow.iterations[0].attempts[0].context_path).toContain(
      `workflow_${wf.workflowId}/iteration_`,
    );

    const plan = snapshot.workflow.iterations[0].attempts[0].plan;
    expect(plan.status).toBe("Success");
    expect(plan.declared_focus).toBe("first half");
    expect(plan.declared_deferred_goal).toBe("second half");
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
    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);

    expect(context.files.get("goal.md")?.content).toBe("the goal");
    expect(context.files.has(legacyGoalPath("current"))).toBe(false);
    expect(context.files.has(legacyGoalPath("original"))).toBe(false);
    expect(context.files.has("outcome.md"), "no outcome before terminal").toBe(false);

    const iteration = tree.iterations[0];
    const attempt = iteration.attempts[0];
    const item = attempt.workItems[0];
    const itemDir = `iteration_${iteration.id}/attempt_${attempt.id}/work_item_${item.id}`;
    expect(context.files.get(`${itemDir}/description.md`)?.content).toBe(
      "implement the slice",
    );
    expect(context.files.get(`${itemDir}/spec.md`)?.content).toBe(
      "write the code for the slice",
    );
    expect(context.files.has(`${itemDir}/summary.md`), "absent until submitted").toBe(
      false,
    );

    const read = readWorkflowContext(context, { path: `${itemDir}/description.md` });
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
    const context = buildWorkflowContext(await h.tree(wf.workflowId));
    const first = readWorkflowContext(context, {
      path: "goal.md",
      maxBytes: 4,
    });
    expect(first.kind).toBe("page");
    if (first.kind !== "page") return;
    expect(first.page.content).toBe("0123");
    expect(first.page.total_bytes).toBe(10);
    expect(first.page.next_offset).toBe(4);

    const rest = readWorkflowContext(context, {
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
    const context = buildWorkflowContext(await h.tree(wf.workflowId));
    const read = readWorkflowContext(context, {});
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

  it("derives the iteration outcome from the closing attempt without archiving the iteration goal on promotion", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal");
    await h.launches[0].submitPlanner(
      plannerPayload({
        iteration_focus: "first half",
        deferred_goal: "second half",
        summary: "the plan summary",
      }),
    );
    await h.launches[1].submitWorker(
      workerPayload({ summary: "worker summary", outcome: "worker outcome" }),
    );
    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);
    const first = tree.iterations[0];
    const closing = first.attempts.at(-1);
    if (!closing) throw new Error("expected a closing attempt");

    const outcome = context.files.get(`iteration_${first.id}/outcome.md`);
    expect(outcome?.content, "iteration outcome = closing attempt outcome").toBe(
      context.files.get(`iteration_${first.id}/attempt_${closing.id}/outcome.md`)
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
      context.directories.has(`archived/iteration_${first.id}`),
      "closed iterations are not copied under a root archive",
    ).toBe(false);
    expect(
      context.files.has(`archived/iteration_${first.id}/goal.md`),
      "the iteration goal has no archived field",
    ).toBe(false);
    expect(context.files.get("goal.md")?.content, "root workflow goal").toBe(
      "whole goal",
    );
  });

  it("workflow Success keeps goal.md live and adds outcome.md", async () => {
    const h = harness();
    const wf = await h.delegate("only goal");
    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(workerPayload());
    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);
    expect(context.files.get("goal.md")?.content).toBe("only goal");
    expect(context.files.has("archived"), "nothing archived without a successor").toBe(
      false,
    );
    const outcome = context.files.get("outcome.md")?.content;
    expect(outcome).toContain(`## iteration_${tree.iterations[0].id} [Success]`);
    expect(outcome, "iteration outcomes carry worker summaries").toContain(
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
    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);
    const iteration = tree.iterations[0];
    const attemptDir = `iteration_${iteration.id}/attempt_${iteration.attempts[0].id}`;

    expect(context.files.get(`${attemptDir}/plan_summary.md`)?.content).toBe(
      "planned the slice",
    );
    expect(
      [...context.directories.keys()].filter((path) =>
        path.split("/").some((segment) => segment.startsWith("plan_")),
      ),
      "no plan_<id>/ directory exists",
    ).toEqual([]);

    const read = readWorkflowContext(context, {});
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
    const tree = await h.tree(wf.workflowId);
    const iteration = tree.iterations[0];
    const attemptDir = `iteration_${iteration.id}/attempt_${iteration.attempts[0].id}`;

    await workerFor("first item").submitWorker(
      workerPayload({ summary: "first summary" }),
    );
    const midway = buildWorkflowContext(await h.tree(wf.workflowId));
    expect(
      midway.files.has(`${attemptDir}/outcome.md`),
      "no attempt outcome before all work items finish",
    ).toBe(false);

    await workerFor("second item").submitWorker(
      workerPayload({ summary: "second summary" }),
    );
    const closed = await h.tree(wf.workflowId);
    const attempt = closed.iterations[0].attempts[0];
    expect(
      attempt.workItems.map((item) => item.description),
      "work items stay in planner order",
    ).toEqual(["first item", "second item"]);
    const context = buildWorkflowContext(closed);
    expect(context.files.get(`${attemptDir}/outcome.md`)?.content).toBe(
      [
        "# Attempt outcome",
        `- work_item_${attempt.workItems[0].id} [Success]: first summary`,
        `- work_item_${attempt.workItems[1].id} [Success]: second summary`,
      ].join("\n"),
    );
  });

  it("a failed attempt with budget left gets fail_reason.md and outcome.md but closes nothing (T4/T5)", async () => {
    const h = harness();
    const wf = await h.delegate("retry goal", 2);
    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "worker failed" }),
    );
    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);
    const iteration = tree.iterations[0];
    const failed = iteration.attempts[0];
    const failedDir = `iteration_${iteration.id}/attempt_${failed.id}`;

    expect(context.files.get(`${failedDir}/fail_reason.md`)?.content).toContain(
      "worker failed",
    );
    expect(context.files.get(`${failedDir}/outcome.md`)?.content).toBe(
      `# Attempt outcome\n- work_item_${failed.workItems[0].id} [Failed]: worker failed`,
    );
    expect(iteration.attempts, "the retry attempt appears").toHaveLength(2);
    expect(
      context.directories.has(
        `iteration_${iteration.id}/attempt_${iteration.attempts[1].id}`,
      ),
      "the retry attempt directory is live",
    ).toBe(true);
    expect(
      context.files.has(`iteration_${iteration.id}/outcome.md`),
      "no iteration outcome while budget remains",
    ).toBe(false);
    expect(context.files.has("outcome.md"), "no workflow outcome yet").toBe(false);
  });

  it("exhausting the retry budget creates attempt, iteration, and workflow outcomes (T6)", async () => {
    const h = harness();
    const wf = await h.delegate("doomed goal", 1);
    await h.launches[0].submitPlanner(plannerPayload());
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "could not do it" }),
    );
    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);
    const iteration = tree.iterations[0];
    const attempt = iteration.attempts[0];
    const attemptOutcome = `# Attempt outcome\n- work_item_${attempt.workItems[0].id} [Failed]: could not do it`;

    expect(tree.workflow.status).toBe("Failed");
    expect(
      context.files.get(`iteration_${iteration.id}/attempt_${attempt.id}/outcome.md`)
        ?.content,
    ).toBe(attemptOutcome);
    expect(
      context.files.get(`iteration_${iteration.id}/outcome.md`)?.content,
      "the failed closing attempt outcome becomes the iteration outcome",
    ).toBe(attemptOutcome);
    expect(
      context.files.get("outcome.md")?.content,
      "the workflow outcome includes the failed iteration outcome",
    ).toBe(`# Workflow outcome\n\n## iteration_${iteration.id} [Failed]\n${attemptOutcome}`);
  });

  it("a planner death renders (no work items) with no plan_summary.md (T1F)", async () => {
    const h = harness();
    const wf = await h.delegate("goal", 2);
    h.launches[0].settle({ status: "failed" });
    await until(() => h.launches.length === 2, "the retry planner launched");

    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);
    const iteration = tree.iterations[0];
    const dead = iteration.attempts[0];
    const deadDir = `iteration_${iteration.id}/attempt_${dead.id}`;

    expect(
      context.files.has(`${deadDir}/plan_summary.md`),
      "a dead planner leaves no summary",
    ).toBe(false);
    expect(context.files.get(`${deadDir}/outcome.md`)?.content).toBe(
      "# Attempt outcome\n(no work items)",
    );
    expect(context.files.get(`${deadDir}/fail_reason.md`)?.content).toContain(
      "without a submission",
    );
  });

  it("a multi-iteration success renders every iteration outcome in sequence order (T7/T8)", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal");
    await h.launches[0].submitPlanner(
      plannerPayload({ iteration_focus: "first half", deferred_goal: "second half" }),
    );
    await h.launches[1].submitWorker(workerPayload({ summary: "first half done" }));

    const midTree = await h.tree(wf.workflowId);
    const midway = buildWorkflowContext(midTree);
    expect(
      midway.files.has(`iteration_${midTree.iterations[0].id}/outcome.md`),
      "the promoted-from iteration closes with an outcome at T7",
    ).toBe(true);
    expect(midway.files.has("outcome.md"), "the workflow is still running at T7").toBe(
      false,
    );

    await h.launches[2].submitPlanner(
      plannerPayload({ iteration_focus: "second half focus" }),
    );
    await h.launches[3].submitWorker(workerPayload({ summary: "second half done" }));

    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);
    const section = (iteration: (typeof tree.iterations)[number]): string => {
      const attempt = iteration.attempts.at(-1);
      const item = attempt?.workItems[0];
      if (!attempt || !item) throw new Error("expected a closing attempt");
      return `## iteration_${iteration.id} [Success]\n# Attempt outcome\n- work_item_${item.id} [Success]: ${item.summary ?? ""}`;
    };
    expect(tree.workflow.status).toBe("Success");
    expect(
      context.files.get("outcome.md")?.content,
      "the workflow outcome is the ordered iteration ledger",
    ).toBe(
      `# Workflow outcome\n\n${section(tree.iterations[0])}\n\n${section(tree.iterations[1])}`,
    );
  });

  it("cancellation renders a marker, never business outcomes for cancelled entities (T10)", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal");
    await h.launches[0].submitPlanner(
      plannerPayload({ iteration_focus: "first half", deferred_goal: "second half" }),
    );
    await h.launches[1].submitWorker(workerPayload({ summary: "first half done" }));
    await wf.cancel("changed direction");

    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);
    const [first, second] = tree.iterations;

    expect(tree.workflow.status).toBe("Cancelled");
    expect(second.status).toBe("Cancelled");
    expect(
      context.files.has(`iteration_${second.id}/outcome.md`),
      "no business outcome for a cancelled iteration",
    ).toBe(false);
    expect(
      context.files.has(
        `iteration_${second.id}/attempt_${second.attempts[0].id}/outcome.md`,
      ),
      "no business outcome for a cancelled attempt",
    ).toBe(false);
    const root = context.files.get("outcome.md")?.content ?? "";
    expect(
      root.startsWith("# Workflow outcome\nworkflow cancelled"),
      "the cancellation marker leads",
    ).toBe(true);
    expect(root, "already closed iterations keep their outcomes").toContain(
      `## iteration_${first.id} [Success]`,
    );
    expect(root).not.toContain(`## iteration_${second.id}`);
  });
});

describe("keep vs refocus paths (§16 case 6)", () => {
  it("relocates drifted attempts whole under archived/ with declaration files on the declarer only", async () => {
    const h = harness();
    const wf = await refocusedWorkflow(h);
    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);
    const iteration = tree.iterations[0];
    const drifted = iteration.attempts[0];
    const live = iteration.attempts[1];
    const archivedDir = `iteration_${iteration.id}/archived/attempt_${drifted.id}`;

    expect(
      context.files.get(`${archivedDir}/focus.md`)?.content,
      "the superseded declaration rides the declaring attempt",
    ).toBe("first direction");
    expect(context.files.get(`${archivedDir}/deferred_goal.md`)?.content).toBe(
      "left for later",
    );
    expect(context.files.get(`${archivedDir}/fail_reason.md`)?.content).toContain(
      "dead end",
    );
    expect(
      context.files.get(`${archivedDir}/plan_summary.md`)?.content,
      "the archived attempt keeps its attempt-owned plan summary",
    ).toBe("planned first direction");
    expect(
      context.files.get(`${archivedDir}/outcome.md`)?.content,
      "the archived attempt keeps its derived outcome",
    ).toContain("dead end");
    const driftedItem = drifted.workItems[0];
    expect(
      context.files.get(
        `${archivedDir}/work_item_${driftedItem.id}/outcome.md`,
      )?.content,
      "the drifted attempt relocates whole, live shapes included",
    ).toBe("hit a wall");

    expect(
      context.directories.has(`iteration_${iteration.id}/attempt_${drifted.id}`),
      "the old live path is gone",
    ).toBe(false);
    expect(
      context.directories.has(`iteration_${iteration.id}/attempt_${live.id}`),
      "the consistent attempt stays live",
    ).toBe(true);
    expect(
      context.files.has(`iteration_${iteration.id}/attempt_${live.id}/focus.md`),
      "live attempts carry no declaration files",
    ).toBe(false);
    expect(
      context.files.get(
        `iteration_${iteration.id}/attempt_${live.id}/plan_summary.md`,
      )?.content,
      "the live attempt owns its own plan summary",
    ).toBe("planned second direction");

    expect(context.files.get(`iteration_${iteration.id}/focus.md`)?.content).toBe(
      "second direction",
    );
    expect(
      context.files.has(`iteration_${iteration.id}/deferred_goal.md`),
      "refocus reset the deferral",
    ).toBe(false);
  });

  it("errors a stale live path naming archived/ among the valid children", async () => {
    const h = harness();
    const wf = await refocusedWorkflow(h);
    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);
    const iteration = tree.iterations[0];
    const drifted = iteration.attempts[0];

    const resolved = resolveContextPath(
      context,
      `iteration_${iteration.id}/attempt_${drifted.id}/fail_reason.md`,
    );
    expect(resolved.kind).toBe("error");
    if (resolved.kind === "error") {
      expect(resolved.message).toContain("archived");
      expect(resolved.message).toContain("unknown context path");
    }
  });

  it("the retry directive after failure carries only consistent attempts and omits the standing deferred_goal", async () => {
    const h = harness();
    await h.delegate("goal", 4);
    await h.launches[0].submitPlanner(
      plannerPayload({
        iteration_focus: "direction one",
        deferred_goal: "the deferral",
      }),
    );
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "failure one" }),
    );
    await h.launches[2].submitPlanner(
      plannerPayload({ iteration_focus: "direction two" }),
    );
    await h.launches[3].submitWorker(
      workerPayload({ is_pass: false, summary: "failure two" }),
    );

    const retryText = allMessageText(h.launches[4].messages);
    expect(retryText).toContain("# Iteration focus\ndirection two");
    expect(retryText).toContain("failure two");
    expect(retryText, "superseded attempts omitted").not.toContain("failure one");
    expect(retryText, "standing deferred_goal omitted").not.toContain("the deferral");
  });

  it("collapses prior iterations and archived rows to status rows in listings", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal", 3);
    await h.launches[0].submitPlanner(
      plannerPayload({ iteration_focus: "first direction", deferred_goal: "later" }),
    );
    await h.launches[1].submitWorker(workerPayload({ is_pass: false }));
    // Refocus carrying a fresh deferral, then close to mint a successor.
    await h.launches[2].submitPlanner(
      plannerPayload({ iteration_focus: "second direction", deferred_goal: "phase 2" }),
    );
    await h.launches[3].submitWorker(workerPayload());
    const tree = await h.tree(wf.workflowId);
    const context = buildWorkflowContext(tree);
    const read = readWorkflowContext(context, {});
    if (read.kind !== "listing") throw new Error("expected a listing");

    const first = tree.iterations[0];
    const priorRows = read.rows.filter((row) =>
      row.path.startsWith(`iteration_${first.id}`),
    );
    expect(priorRows, "prior iteration collapses to one status row").toHaveLength(1);
    expect(priorRows[0].status).toBe("Success");

    const rootArchiveRows = read.rows.filter((row) => row.path.startsWith("archived/"));
    expect(rootArchiveRows, "no root archive rows remain").toHaveLength(0);

    const insideArchived = readWorkflowContext(context, {
      path: `iteration_${first.id}/archived/attempt_${first.attempts[0].id}`,
    });
    expect(insideArchived.kind, "archived files stay readable at full fidelity").toBe(
      "listing",
    );
    if (insideArchived.kind === "listing") {
      expect(
        insideArchived.rows.some((row) => row.path.endsWith("focus.md")),
      ).toBe(true);
    }

    const listing = listContextSubtree(context, "");
    expect(listing.length).toBe(read.rows.length);
  });
});
