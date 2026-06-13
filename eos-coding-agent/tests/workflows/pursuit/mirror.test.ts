import { access, readFile } from "node:fs/promises";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { harness, plannerPayload, workerPayload, workItem } from "./support.js";

describe("pursuit context mirror", () => {
  it("writes the pursuit context under the pursuit mirror root", async () => {
    const h = harness();
    const pursuit = await h.create("ship mirror");
    const root = join(h.contextRoot, `pursuit_${pursuit.pursuit_id}`);

    await expect(readFile(join(root, "goal.md"), "utf8")).resolves.toBe(
      "ship mirror",
    );
  });

  it("prunes relocated live paths when an attempt becomes superseded", async () => {
    const h = harness();
    const pursuit = await h.create("whole goal", { maxAttempts: 3 });
    await h.launches[0].submitPlanner(
      plannerPayload({ next_leg_goal: "later", work_items: [workItem("old")] }),
    );
    await h.launches[1].submitWorker(
      workerPayload({ is_pass: false, summary: "old failed" }),
    );
    const oldTree = await h.tree(pursuit.pursuit_id);
    const leg = oldTree.legs[0];
    const oldAttempt = leg.attempts[0];
    const oldLivePath = join(
      h.contextRoot,
      `pursuit_${pursuit.pursuit_id}`,
      `leg_${leg.id}`,
      `attempt_${oldAttempt.id}`,
    );

    await h.launches[2].submitPlanner(
      plannerPayload({ leg_goal: "new goal", work_items: [workItem("old")] }),
    );

    const supersededNextGoalPath = join(
      h.contextRoot,
      `pursuit_${pursuit.pursuit_id}`,
      `leg_${leg.id}`,
      "superseded",
      `attempt_${oldAttempt.id}`,
      "next_leg_goal.md",
    );
    await expect
      .poll(async () => {
        try {
          await access(oldLivePath);
          return null;
        } catch {
          // The old live path has been pruned; now verify the relocated file.
        }
        try {
          return await readFile(supersededNextGoalPath, "utf8");
        } catch {
          return null;
        }
      }, { timeout: 8_000 })
      .toBe("later");
    await expect(access(oldLivePath)).rejects.toThrow();
    await expect(readFile(supersededNextGoalPath, "utf8")).resolves.toBe("later");
  }, 10_000);
});
