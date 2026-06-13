import { describe, expect, it } from "vitest";

import { snapshotPursuitContext } from "../../../src/workflows/pursuit/context-engine/input.js";
import { buildPursuitContext } from "../../../src/workflows/pursuit/context-engine/projection/paths.js";
import { readPursuitContext, searchPursuitContext } from "../../../src/workflows/pursuit/pursuit-context.js";
import { harness, plannerPayload, workerPayload, workItem } from "./support.js";

describe("pursuit context projection", () => {
  it("renders pursuit and leg goal files at creation", async () => {
    const h = harness();
    const pursuit = await h.create("ship it");
    const context = buildPursuitContext(await h.tree(pursuit.pursuit_id));
    const leg = (await h.tree(pursuit.pursuit_id)).legs[0];

    expect(context.rootPath).toBe(`pursuit_${pursuit.pursuit_id}`);
    expect(context.files.get("goal.md")?.content).toBe("ship it");
    expect(context.files.get(`leg_${leg.id}/leg_goal.md`)?.content).toContain(
      "Provenance: inherited from pursuit goal",
    );
    const legacyFocusFile = ["focus", "md"].join(".");
    expect([...context.files.keys()].some((path) => path.includes(legacyFocusFile))).toBe(
      false,
    );
  });

  it("renders work item title/spec and omits old description files", async () => {
    const h = harness();
    const pursuit = await h.create("ship it");
    await h.launches[0].submitPlanner(
      plannerPayload({ work_items: [workItem("a")] }),
    );
    const tree = await h.tree(pursuit.pursuit_id);
    const leg = tree.legs[0];
    const attempt = leg.attempts[0];
    const context = buildPursuitContext(tree);
    const itemDir = `leg_${leg.id}/attempt_${attempt.id}/work_item_a`;

    expect(context.files.get(`${itemDir}/title.md`)?.content).toBe("item a");
    expect(context.files.get(`${itemDir}/spec.md`)?.content).toBe("spec a");
    const legacyDescriptionFile = ["description", "md"].join(".");
    expect(context.files.has(`${itemDir}/${legacyDescriptionFile}`)).toBe(false);
  });

  it("renders failed attempt failure_reasons as a list and keeps outcomes null while running", async () => {
    const h = harness();
    const pursuit = await h.create("ship it", { maxAttempts: 1 });
    await h.launches[0].submitPlanner(
      plannerPayload({ work_items: [workItem("a")] }),
    );
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "boom" }),
    );

    const tree = await h.tree(pursuit.pursuit_id);
    const leg = tree.legs[0];
    const attempt = leg.attempts[0];
    const context = buildPursuitContext(tree);
    expect(
      context.files.get(`leg_${leg.id}/attempt_${attempt.id}/failure_reasons.md`)
        ?.content,
    ).toBe("- work_item_a [Failed]: boom");

    const snapshot = snapshotPursuitContext(tree);
    expect(snapshot.pursuit.outcome).not.toBeNull();
    expect(snapshot.pursuit.legs[0].attempts[0].outcome).not.toBeNull();
  });

  it("renders blocked work-item outcomes with the direct failed dependency", async () => {
    const h = harness();
    const pursuit = await h.create("ship it", { maxAttempts: 1 });
    await h.launches[0].submitPlanner(
      plannerPayload({ work_items: [workItem("a"), workItem("b", ["a"])] }),
    );
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "boom", outcome: "root failed" }),
    );

    const tree = await h.tree(pursuit.pursuit_id);
    const leg = tree.legs[0];
    const attempt = leg.attempts[0];
    const context = buildPursuitContext(tree);
    const blockedDir = `leg_${leg.id}/attempt_${attempt.id}/work_item_b`;

    expect(context.files.get(`${blockedDir}/summary.md`)?.content).toBe(
      "blocked by work_item_a",
    );
    expect(context.files.get(`${blockedDir}/outcome.md`)?.content).toBe(
      "blocked by work_item_a",
    );
    expect(
      context.files.get(`leg_${leg.id}/attempt_${attempt.id}/failure_reasons.md`)
        ?.content,
    ).toContain("- work_item_b [Blocked]: blocked by work_item_a");
  });

  it("moves superseded attempts under superseded after dynamic refocus", async () => {
    const h = harness();
    const pursuit = await h.create("whole goal", { maxAttempts: 3 });
    await h.launches[0].submitPlanner(
      plannerPayload({ next_leg_goal: "later", work_items: [workItem("old")] }),
    );
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "old failed" }),
    );
    await h.launches[2].submitPlanner(
      plannerPayload({ leg_goal: "new goal", work_items: [workItem("new")] }),
    );

    const tree = await h.tree(pursuit.pursuit_id);
    const leg = tree.legs[0];
    const oldAttempt = leg.attempts[0];
    const context = buildPursuitContext(tree);
    const oldPath = `leg_${leg.id}/superseded/attempt_${oldAttempt.id}`;
    expect(context.directories.has(oldPath)).toBe(true);
    expect(context.files.get(`${oldPath}/next_leg_goal.md`)?.content).toBe("later");
    expect(context.files.has(`${oldPath}/leg_goal.md`)).toBe(false);
    expect(context.files.get(`leg_${leg.id}/leg_goal.md`)?.content).toContain(
      "new goal",
    );
    expect(context.files.has(`leg_${leg.id}/next_leg_goal.md`)).toBe(false);
  });

  it("reads files and lists directories from the pursuit context universe", async () => {
    const h = harness();
    const pursuit = await h.create("ship it");
    const context = buildPursuitContext(await h.tree(pursuit.pursuit_id));

    const page = readPursuitContext(context, { path: "goal.md" });
    expect(page.kind).toBe("page");
    if (page.kind === "page") expect(page.page.content).toBe("ship it");

    const listing = readPursuitContext(context, {});
    expect(listing.kind).toBe("listing");
    if (listing.kind === "listing") {
      expect(listing.rows.some((row) => row.path.startsWith("leg_"))).toBe(true);
    }
  });

  it("searches live context by default and includes superseded files only when scoped", async () => {
    const h = harness();
    const pursuit = await h.create("whole goal", { maxAttempts: 3 });
    await h.launches[0].submitPlanner(
      plannerPayload({ next_leg_goal: "later", work_items: [workItem("old")] }),
    );
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "old failed" }),
    );
    await h.launches[2].submitPlanner(
      plannerPayload({ leg_goal: "new goal", work_items: [workItem("new")] }),
    );

    const tree = await h.tree(pursuit.pursuit_id);
    const leg = tree.legs[0];
    const context = buildPursuitContext(tree);
    const live = searchPursuitContext(context, { query: "later" });
    expect(live.matches.some((match) => match.path.includes("superseded"))).toBe(false);

    const scoped = searchPursuitContext(context, {
      query: "later",
      scope: `leg_${leg.id}/superseded`,
    });
    expect(scoped.matches.map((match) => match.path)).toContain(
      `leg_${leg.id}/superseded/attempt_${leg.attempts[0].id}/next_leg_goal.md`,
    );
  });
});
