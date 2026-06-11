import { chmodSync, existsSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { join } from "node:path";

import type { WorkflowId } from "@eos/contracts";
import { describe, expect, it } from "vitest";

import { buildWorkflowContext } from "../src/archive/paths.js";
import { readWorkflowContext } from "../src/workflow-context.js";
import {
  harness,
  plannerPayload,
  workerPayload,
  type Harness,
} from "./support.js";

function diskFiles(root: string): Map<string, string> {
  const files = new Map<string, string>();
  if (!existsSync(root)) return files;
  for (const entry of readdirSync(root, { recursive: true })) {
    const relative = String(entry);
    const full = join(root, relative);
    if (statSync(full).isFile()) {
      files.set(relative.split("\\").join("/"), readFileSync(full, "utf8"));
    }
  }
  return files;
}

function diskDirectories(root: string): string[] {
  if (!existsSync(root)) return [];
  return readdirSync(root, { recursive: true })
    .map((entry) => String(entry).split("\\").join("/"))
    .filter((relative) => statSync(join(root, relative)).isDirectory());
}

async function expectMirrorEqualsUniverse(h: Harness, workflowId: WorkflowId) {
  const context = buildWorkflowContext(await h.tree(workflowId));
  const disk = diskFiles(join(h.contextRoot, context.rootPath));
  expect(
    [...disk.keys()].sort(),
    "the on-disk tree equals the rendered universe",
  ).toEqual([...context.files.keys()].sort());
  for (const [path, entry] of context.files) {
    expect(disk.get(path), `byte-for-byte content of ${path}`).toBe(entry.content);
  }
  expect(
    diskDirectories(join(h.contextRoot, context.rootPath)).filter((path) =>
      path.split("/").some((segment) => segment.startsWith("plan_")),
    ),
    "no plan_<id>/ directories exist on disk",
  ).toEqual([]);
  return context;
}

describe("disk mirror (§16 case 13)", () => {
  it("mirrors the universe byte-for-byte after each lifecycle step and prunes on refocus", async () => {
    const h = harness();
    const wf = await h.delegate("whole goal", 3);
    await expectMirrorEqualsUniverse(h, wf.workflowId);

    await h.launches[0].submitPlanner(
      plannerPayload({ iteration_focus: "first direction", deferred_goal: "later" }),
    );
    await expectMirrorEqualsUniverse(h, wf.workflowId);

    await h.launches[1].submitWorker(workerPayload({ is_pass: false }));
    await expectMirrorEqualsUniverse(h, wf.workflowId);

    const tree = await h.tree(wf.workflowId);
    const iteration = tree.iterations[0];
    const drifted = iteration.attempts[0];
    const oldLivePath = join(
      h.contextRoot,
      `workflow_${wf.workflowId}`,
      `iteration_${iteration.id}`,
      `attempt_${drifted.id}`,
    );
    expect(existsSync(oldLivePath), "live before the refocus").toBe(true);

    await h.launches[2].submitPlanner(
      plannerPayload({ iteration_focus: "second direction" }),
    );
    const context = await expectMirrorEqualsUniverse(h, wf.workflowId);
    expect(existsSync(oldLivePath), "the old live attempt folder was pruned").toBe(
      false,
    );
    expect(
      existsSync(
        join(
          h.contextRoot,
          `workflow_${wf.workflowId}`,
          `iteration_${iteration.id}`,
          "archived",
          `attempt_${drifted.id}`,
          "focus.md",
        ),
      ),
      "the archived folder was written",
    ).toBe(true);
    expect(context.files.size).toBeGreaterThan(0);
  });

  it("treats a write failure as non-fatal and heals on the next mutation", async () => {
    const failures: unknown[] = [];
    const h = harness({
      logMirrorFailure: (_workflowId, error) => {
        failures.push(error);
      },
    });
    const wf = await h.delegate();
    const root = join(h.contextRoot, `workflow_${wf.workflowId}`);

    chmodSync(root, 0o555);
    try {
      const result = await h.launches[0].submitPlanner(plannerPayload());
      expect(result.ok, "the mutation itself is unaffected").toBe(true);
      expect(failures.length, "the failure was logged").toBeGreaterThan(0);
      const tree = await h.tree(wf.workflowId);
      expect(tree.iterations[0].focus, "DB state landed").toBe("the first slice");
    } finally {
      chmodSync(root, 0o755);
    }

    // The next mutation re-projects the whole workflow and heals the mirror.
    await h.launches[1].submitWorker(workerPayload());
    await expectMirrorEqualsUniverse(h, wf.workflowId);
  });

  it("package-side resolver and listing output is identical with the mirror deleted", async () => {
    const h = harness();
    const wf = await h.delegate();
    await h.launches[0].submitPlanner(plannerPayload());

    const context = buildWorkflowContext(await h.tree(wf.workflowId));
    const before = readWorkflowContext(context, {});
    rmSync(join(h.contextRoot, `workflow_${wf.workflowId}`), {
      recursive: true,
      force: true,
    });
    const after = readWorkflowContext(
      buildWorkflowContext(await h.tree(wf.workflowId)),
      {},
    );
    expect(after, "rendering never reads the mirror").toEqual(before);
  });
});
