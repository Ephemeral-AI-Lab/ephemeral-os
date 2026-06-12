import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import type { PursuitId } from "@eos/contracts";
import { beforeAll, describe, expect, it } from "vitest";

import { buildPursuitContext, type PursuitContext } from "../src/archive/paths.js";
import type { PursuitTree } from "../src/pursuit-tree.js";
import {
  allMessageText,
  harness,
  plannerPayload,
  until,
  workerPayload,
  type Harness,
  type ScriptedLaunch,
} from "./support.js";

// ---------------------------------------------------------------------------
// Phase 05.2 §6-§8 case registry. Every test names the spec rows it supports;
// the closing matrix asserts each row is supported by at least five tests.
// ---------------------------------------------------------------------------

const SPEC_CASES = [
  // §6 logical creation schedule
  "T0",
  "T1",
  "T1F",
  "T2",
  "T3",
  "T4",
  "T5",
  "T6",
  "T7",
  "T8",
  "T9",
  "T10",
  // §7 directory first-appearance rows
  "dir pursuit_<id>/",
  "dir leg_<id>/",
  "dir attempt_<id>/",
  "dir superseded/",
  "dir superseded/attempt_<id>/",
  "dir work_item_<id>/",
  "dir plan_<id>/ never exists",
  // §8 file first-appearance rows
  "file goal.md",
  "file root outcome.md",
  "file leg_goal.md",
  "file next_leg_goal.md",
  "file leg outcome.md",
  "file plan_summary.md",
  "file failure_reasons.md",
  "file attempt outcome.md",
  "file description.md",
  "file spec.md",
  "file work-item summary.md",
  "file work-item outcome.md",
  "file superseded leg_goal.md",
  "file superseded next_leg_goal.md",
] as const;
type SpecCase = (typeof SPEC_CASES)[number];

const support = new Map<SpecCase, string[]>(SPEC_CASES.map((id) => [id, []]));

function covers(
  cases: readonly SpecCase[],
  title: string,
  run: () => void | Promise<void>,
): void {
  for (const id of new Set(cases)) support.get(id)?.push(title);
  it(title, run);
}

// ---------------------------------------------------------------------------
// Projection-point snapshots: the in-memory universe plus the disk mirror,
// captured after each committed mutation (the §6 "stable test contract").
// ---------------------------------------------------------------------------

interface Snap {
  readonly label: string;
  readonly tree: PursuitTree;
  readonly context: PursuitContext;
  readonly disk: ReadonlyMap<string, string>;
  readonly diskDirs: ReadonlySet<string>;
}

function readDisk(root: string): { files: Map<string, string>; dirs: Set<string> } {
  const files = new Map<string, string>();
  const dirs = new Set<string>();
  if (!existsSync(root)) return { files, dirs };
  for (const entry of readdirSync(root, { recursive: true })) {
    const relative = String(entry).split("\\").join("/");
    if (statSync(join(root, relative)).isDirectory()) dirs.add(relative);
    else files.set(relative, readFileSync(join(root, relative), "utf8"));
  }
  return { files, dirs };
}

async function snap(h: Harness, pursuitId: PursuitId, label: string): Promise<Snap> {
  const tree = await h.tree(pursuitId);
  const context = buildPursuitContext(tree);
  const { files, dirs } = readDisk(join(h.contextRoot, context.rootPath));
  return { label, tree, context, disk: files, diskDirs: dirs };
}

const filePaths = (s: Snap): string[] => [...s.context.files.keys()].sort();
const dirPaths = (s: Snap): string[] => [...s.context.directories.keys()].sort();
const content = (s: Snap, path: string): string | undefined =>
  s.context.files.get(path)?.content;
const has = (s: Snap, path: string): boolean => s.context.files.has(path);
const hasDir = (s: Snap, path: string): boolean => s.context.directories.has(path);
const appeared = (before: Snap, after: Snap): string[] =>
  filePaths(after).filter((path) => !before.context.files.has(path));
const removedFiles = (before: Snap, after: Snap): string[] =>
  filePaths(before).filter((path) => !after.context.files.has(path));

/** Paths owning a `plan_` segment, `plan_summary.md` excepted (§7 last row). */
const planSegments = (paths: Iterable<string>): string[] =>
  [...paths].filter((path) =>
    path
      .split("/")
      .some((segment) => segment.startsWith("plan_") && segment !== "plan_summary.md"),
  );

function expectMirrorParity(s: Snap): void {
  expect(
    [...s.disk.keys()].sort(),
    `${s.label}: the disk file set equals the rendered universe`,
  ).toEqual(filePaths(s));
  for (const [path, entry] of s.context.files) {
    expect(s.disk.get(path), `${s.label}: byte-for-byte content of ${path}`).toBe(
      entry.content,
    );
  }
  expect(
    [...s.diskDirs].sort(),
    `${s.label}: the disk directory set equals the rendered universe`,
  ).toEqual(dirPaths(s).filter((path) => path !== ""));
}

function workerFor(h: Harness, description: string): ScriptedLaunch {
  const launch = h.launches.find(
    (candidate) =>
      candidate.agentName === "worker" &&
      allMessageText(candidate.messages).includes(description),
  );
  if (!launch) throw new Error(`no worker launch saw "${description}"`);
  return launch;
}

function workItem(id: string, description: string, spec: string) {
  return { id, agent_name: "worker", description, work_item_spec: spec, needs: [] };
}

// ---------------------------------------------------------------------------
// Scenario A - single-leg success with two parallel work items.
// Points: T0 (delegate), T1 (planner), T2 (first worker), T3/T8 (second).
// ---------------------------------------------------------------------------

describe("creation schedule: single-leg success (T0→T1→T2→T3/T8)", () => {
  let t0: Snap, t1: Snap, t2: Snap, t8: Snap;
  let points: Snap[];
  let iterDir: string, attemptDir: string, w1Dir: string, w2Dir: string;
  let iterId: string, w1Id: string, w2Id: string;

  beforeAll(async () => {
    const h = harness();
    const wf = await h.delegate("ship both items");
    t0 = await snap(h, wf.pursuitId, "T0");

    await h.launches[0].submitPlanner(
      plannerPayload({
        summary: "planned both items",
        leg_goal: "the only slice",
        work_items: [
          workItem("w1", "first item", "spec one"),
          workItem("w2", "second item", "spec two"),
        ],
      }),
    );
    t1 = await snap(h, wf.pursuitId, "T1");

    const leg = t1.tree.legs[0];
    const attempt = leg.attempts[0];
    iterId = leg.id;
    [w1Id, w2Id] = attempt.workItems.map((item) => item.id);
    iterDir = `leg_${leg.id}`;
    attemptDir = `${iterDir}/attempt_${attempt.id}`;
    w1Dir = `${attemptDir}/work_item_${w1Id}`;
    w2Dir = `${attemptDir}/work_item_${w2Id}`;

    await workerFor(h, "first item").submitWorker(
      workerPayload({ summary: "first summary", outcome: "first outcome" }),
    );
    t2 = await snap(h, wf.pursuitId, "T2");

    await workerFor(h, "second item").submitWorker(
      workerPayload({ summary: "second summary", outcome: "second outcome" }),
    );
    t8 = await snap(h, wf.pursuitId, "T3/T8");
    points = [t0, t1, t2, t8];
  });

  covers(
    ["T0", "dir pursuit_<id>/", "dir leg_<id>/", "dir attempt_<id>/", "file goal.md", "dir plan_<id>/ never exists"],
    "T0: delegation projects exactly the pursuit root, first leg, and first attempt directories",
    () => {
      expect(dirPaths(t0)).toEqual(["", iterDir, attemptDir]);
      expect(t0.context.directories.get("")?.owner.kind).toBe("pursuit");
      expect(content(t0, "goal.md")).toBe("ship both items");
      expect(t0.tree.legs, "one leg at T0").toHaveLength(1);
      expect(t0.tree.legs[0].attempts, "one attempt at T0").toHaveLength(1);
    },
  );

  covers(
    ["T0", "file leg_goal.md", "file next_leg_goal.md", "file plan_summary.md", "file description.md", "file spec.md", "dir work_item_<id>/"],
    "T0: only goal.md exists - no declaration, plan-summary, or work-item files before the planner submits",
    () => {
      expect(filePaths(t0)).toEqual(["goal.md"]);
      expect(
        dirPaths(t0).filter((path) => path.includes("work_item_")),
        "no work-item directory before T1",
      ).toEqual([]);
    },
  );

  covers(
    ["T1", "file leg_goal.md", "file plan_summary.md", "file description.md", "file spec.md", "dir work_item_<id>/", "dir attempt_<id>/"],
    "T1: an accepted planner payload renders focus, plan summary, and both work items' static files in one commit",
    () => {
      expect(content(t1, `${iterDir}/leg_goal.md`)).toBe("the only slice");
      expect(content(t1, `${attemptDir}/plan_summary.md`)).toBe("planned both items");
      expect(hasDir(t1, w1Dir), "first work-item directory appears at T1").toBe(true);
      expect(hasDir(t1, w2Dir), "second work-item directory appears at T1").toBe(true);
      expect(content(t1, `${w1Dir}/description.md`)).toBe("first item");
      expect(content(t1, `${w1Dir}/spec.md`)).toBe("spec one");
      expect(content(t1, `${w2Dir}/description.md`)).toBe("second item");
      expect(content(t1, `${w2Dir}/spec.md`)).toBe("spec two");
    },
  );

  covers(
    ["T1", "file next_leg_goal.md", "file work-item summary.md", "file work-item outcome.md", "file attempt outcome.md", "file leg outcome.md", "file root outcome.md"],
    "T1: a declaration without a deferral renders no next_leg_goal.md, and no worker or outcome files exist yet",
    () => {
      expect(has(t1, `${iterDir}/next_leg_goal.md`), "no deferral declared").toBe(false);
      for (const dir of [w1Dir, w2Dir]) {
        expect(has(t1, `${dir}/summary.md`), `${dir} has no summary before T2`).toBe(false);
        expect(has(t1, `${dir}/outcome.md`), `${dir} has no outcome before T2`).toBe(false);
      }
      expect(has(t1, `${attemptDir}/outcome.md`), "no attempt outcome at T1").toBe(false);
      expect(has(t1, `${iterDir}/outcome.md`), "no leg outcome at T1").toBe(false);
      expect(has(t1, "outcome.md"), "no pursuit outcome at T1").toBe(false);
    },
  );

  covers(
    ["T1", "T2", "file work-item summary.md", "file work-item outcome.md"],
    "T2: the file-universe diff of one worker submission is exactly that work item's summary.md and outcome.md",
    () => {
      expect(appeared(t1, t2)).toEqual([`${w1Dir}/outcome.md`, `${w1Dir}/summary.md`]);
      expect(removedFiles(t1, t2), "nothing disappears at T2").toEqual([]);
      expect(content(t2, `${w1Dir}/summary.md`)).toBe("first summary");
      expect(content(t2, `${w1Dir}/outcome.md`)).toBe("first outcome");
    },
  );

  covers(
    ["T2", "file attempt outcome.md", "file leg outcome.md", "file root outcome.md", "file work-item summary.md", "file work-item outcome.md"],
    "T2: the attempt stays Running with no outcome files anywhere while its sibling work item is unfinished",
    () => {
      expect(t2.tree.legs[0].attempts[0].status).toBe("Running");
      expect(has(t2, `${attemptDir}/outcome.md`), "no attempt outcome mid-attempt").toBe(false);
      expect(has(t2, `${iterDir}/outcome.md`), "no leg outcome mid-attempt").toBe(false);
      expect(has(t2, "outcome.md"), "no pursuit outcome mid-attempt").toBe(false);
      expect(has(t2, `${w2Dir}/summary.md`), "the sibling has not submitted").toBe(false);
      expect(has(t2, `${w2Dir}/outcome.md`), "the sibling has not submitted").toBe(false);
    },
  );

  covers(
    ["T3", "T8", "file attempt outcome.md", "file leg outcome.md", "file root outcome.md", "file work-item summary.md", "file work-item outcome.md"],
    "T3/T8: the final worker success renders attempt, leg, and pursuit outcomes in one commit",
    () => {
      expect(appeared(t2, t8)).toEqual([
        `${attemptDir}/outcome.md`,
        `${attemptDir}/work_item_${w2Id}/outcome.md`,
        `${attemptDir}/work_item_${w2Id}/summary.md`,
        `${iterDir}/outcome.md`,
        "outcome.md",
      ]);
      expect(removedFiles(t2, t8), "nothing disappears at T3/T8").toEqual([]);
      const attemptOutcome = [
        "# Attempt outcome",
        `- work_item_${w1Id} [Success]: first summary`,
        `- work_item_${w2Id} [Success]: second summary`,
      ].join("\n");
      expect(content(t8, `${attemptDir}/outcome.md`), "planner order").toBe(attemptOutcome);
      expect(content(t8, `${iterDir}/outcome.md`), "leg = closing attempt").toBe(
        attemptOutcome,
      );
      expect(content(t8, "outcome.md"), "pursuit = leg ledger").toBe(
        `# Pursuit outcome\n\n## leg_${iterId} [Success]\n${attemptOutcome}`,
      );
    },
  );

  covers(
    ["T8", "file goal.md", "file root outcome.md", "dir pursuit_<id>/", "dir superseded/"],
    "T8: goal.md survives terminal success, the root never moves, and nothing is superseded without a refocus",
    () => {
      expect(t8.tree.pursuit.status).toBe("Success");
      for (const point of points) {
        expect(content(point, "goal.md"), `${point.label}: goal.md is stable`).toBe(
          "ship both items",
        );
        expect(point.context.rootPath, `${point.label}: root path is stable`).toBe(
          t0.context.rootPath,
        );
        expect(
          dirPaths(point).filter((path) => path.split("/").includes("superseded")),
          `${point.label}: no superseded/ directory without a refocus`,
        ).toEqual([]);
      }
    },
  );

  covers(
    ["dir plan_<id>/ never exists", "file plan_summary.md"],
    "no plan_<id>/ segment exists in memory or on disk at any single-leg point",
    () => {
      for (const point of points) {
        expect(planSegments(filePaths(point)), `${point.label}: files`).toEqual([]);
        expect(planSegments(dirPaths(point)), `${point.label}: directories`).toEqual([]);
        expect(planSegments(point.disk.keys()), `${point.label}: disk files`).toEqual([]);
        expect(planSegments(point.diskDirs), `${point.label}: disk directories`).toEqual([]);
      }
      expect(has(t1, `${attemptDir}/plan_summary.md`), "the flattened file remains").toBe(true);
    },
  );

  covers(
    ["T1", "T2", "T3", "dir work_item_<id>/", "file description.md", "file spec.md"],
    "work-item directories and static files persist unchanged from acceptance through terminal",
    () => {
      for (const point of [t1, t2, t8]) {
        for (const dir of [w1Dir, w2Dir]) {
          expect(hasDir(point, dir), `${point.label}: ${dir} exists`).toBe(true);
          expect(
            content(point, `${dir}/description.md`),
            `${point.label}: ${dir} description is stable`,
          ).toBe(content(t1, `${dir}/description.md`));
          expect(
            content(point, `${dir}/spec.md`),
            `${point.label}: ${dir} spec is stable`,
          ).toBe(content(t1, `${dir}/spec.md`));
        }
      }
    },
  );

  covers(
    ["T2", "file work-item summary.md", "file work-item outcome.md", "dir work_item_<id>/"],
    "T2: the sibling work item's directory and static files are untouched by the first submission",
    () => {
      expect(hasDir(t2, w2Dir)).toBe(true);
      expect(content(t2, `${w2Dir}/description.md`)).toBe("second item");
      expect(content(t2, `${w2Dir}/spec.md`)).toBe("spec two");
      expect(
        filePaths(t2).filter((path) => path.startsWith(`${w2Dir}/`)),
        "static files only until the sibling submits",
      ).toEqual([`${w2Dir}/description.md`, `${w2Dir}/spec.md`]);
    },
  );

  covers(
    ["T0", "T1", "T2", "T3", "T8", "dir pursuit_<id>/", "dir plan_<id>/ never exists"],
    "the disk mirror equals the in-memory universe at every single-leg projection point",
    () => {
      for (const point of points) expectMirrorParity(point);
    },
  );
});

// ---------------------------------------------------------------------------
// Scenario B - deferred promotion across two legs.
// Points: T0, T1 (declare + deferral), T7 (promote), T1 again, T2, T8.
// ---------------------------------------------------------------------------

describe("creation schedule: deferred-goal promotion (T7) into a multi-leg success (T8)", () => {
  let t0: Snap, t1: Snap, t7: Snap, t1b: Snap, t2b: Snap, t8: Snap;
  let points: Snap[];
  let iter1Dir: string, iter2Dir: string, attempt1Dir: string, attempt2Dir: string;
  let iter1Id: string, iter2Id: string;
  let b1Id: string, b2Id: string, b3Id: string;

  beforeAll(async () => {
    const h = harness();
    const wf = await h.delegate("whole goal");
    t0 = await snap(h, wf.pursuitId, "T0");

    await h.launches[0].submitPlanner(
      plannerPayload({
        summary: "planned first half",
        leg_goal: "first half",
        next_leg_goal: "second half",
        work_items: [workItem("b1", "item one", "spec b1")],
      }),
    );
    t1 = await snap(h, wf.pursuitId, "T1");
    const iter1 = t1.tree.legs[0];
    iter1Id = iter1.id;
    iter1Dir = `leg_${iter1.id}`;
    attempt1Dir = `${iter1Dir}/attempt_${iter1.attempts[0].id}`;
    b1Id = iter1.attempts[0].workItems[0].id;

    await workerFor(h, "item one").submitWorker(
      workerPayload({ summary: "first half done", outcome: "first details" }),
    );
    t7 = await snap(h, wf.pursuitId, "T7");

    await h.launches[2].submitPlanner(
      plannerPayload({
        summary: "planned second half",
        leg_goal: "second focus",
        work_items: [
          workItem("b2", "item two", "spec b2"),
          workItem("b3", "item three", "spec b3"),
        ],
      }),
    );
    t1b = await snap(h, wf.pursuitId, "T1 (promoted leg)");
    const iter2 = t1b.tree.legs[1];
    iter2Id = iter2.id;
    iter2Dir = `leg_${iter2.id}`;
    attempt2Dir = `${iter2Dir}/attempt_${iter2.attempts[0].id}`;
    [b2Id, b3Id] = iter2.attempts[0].workItems.map((item) => item.id);

    await workerFor(h, "item two").submitWorker(
      workerPayload({ summary: "item two done", outcome: "two details" }),
    );
    t2b = await snap(h, wf.pursuitId, "T2 (promoted leg)");

    await workerFor(h, "item three").submitWorker(
      workerPayload({ summary: "item three done", outcome: "three details" }),
    );
    t8 = await snap(h, wf.pursuitId, "T8");
    points = [t0, t1, t7, t1b, t2b, t8];
  });

  covers(
    ["T1", "file leg_goal.md", "file next_leg_goal.md"],
    "T1: a declaration with a deferral renders both leg_goal.md and next_leg_goal.md verbatim",
    () => {
      expect(content(t1, `${iter1Dir}/leg_goal.md`)).toBe("first half");
      expect(content(t1, `${iter1Dir}/next_leg_goal.md`)).toBe("second half");
    },
  );

  covers(
    ["T7", "dir leg_<id>/", "dir attempt_<id>/", "file leg outcome.md", "file leg_goal.md"],
    "T7: closing with a standing deferral promotes a second leg with a first attempt directory and no focus yet",
    () => {
      expect(t7.tree.legs, "the promoted leg exists").toHaveLength(2);
      const promoted = t7.tree.legs[1];
      const promotedDir = `leg_${promoted.id}`;
      expect(hasDir(t7, promotedDir), "the promoted leg directory appears").toBe(true);
      expect(
        hasDir(t7, `${promotedDir}/attempt_${promoted.attempts[0].id}`),
        "the promoted leg's first attempt directory appears",
      ).toBe(true);
      expect(has(t7, `${promotedDir}/leg_goal.md`), "no focus before its declaration").toBe(false);
      expect(has(t7, `${iter1Dir}/outcome.md`), "the closed leg has its outcome").toBe(true);
    },
  );

  covers(
    ["T7", "file root outcome.md"],
    "T7: the pursuit stays Running with no root outcome.md while the promoted leg runs",
    () => {
      expect(t7.tree.pursuit.status).toBe("Running");
      expect(has(t7, "outcome.md")).toBe(false);
    },
  );

  covers(
    ["T3", "T7", "file leg outcome.md", "file attempt outcome.md"],
    "T7: the closed leg's outcome.md equals its closing attempt's outcome.md",
    () => {
      const attemptOutcome = content(t7, `${attempt1Dir}/outcome.md`);
      expect(attemptOutcome).toBe(
        `# Attempt outcome\n- work_item_${b1Id} [Success]: first half done`,
      );
      expect(content(t7, `${iter1Dir}/outcome.md`)).toBe(attemptOutcome);
    },
  );

  covers(
    ["T1", "file leg_goal.md", "file next_leg_goal.md", "file plan_summary.md", "dir work_item_<id>/", "file description.md", "file spec.md"],
    "T1 in a promoted leg: the second declaration renders under leg 2 only, leaving leg 1 unchanged",
    () => {
      expect(content(t1b, `${iter2Dir}/leg_goal.md`)).toBe("second focus");
      expect(content(t1b, `${attempt2Dir}/plan_summary.md`)).toBe("planned second half");
      expect(content(t1b, `${attempt2Dir}/work_item_${b2Id}/description.md`)).toBe("item two");
      expect(content(t1b, `${attempt2Dir}/work_item_${b3Id}/spec.md`)).toBe("spec b3");
      expect(content(t1b, `${iter1Dir}/leg_goal.md`), "leg 1 focus is untouched").toBe(
        "first half",
      );
      expect(
        content(t1b, `${iter1Dir}/next_leg_goal.md`),
        "leg 1 deferral is untouched",
      ).toBe("second half");
    },
  );

  covers(
    ["T8", "file root outcome.md", "file leg outcome.md"],
    "T8: the root outcome lists every leg outcome in sequence order",
    () => {
      expect(t8.tree.pursuit.status).toBe("Success");
      const first = `# Attempt outcome\n- work_item_${b1Id} [Success]: first half done`;
      const second = [
        "# Attempt outcome",
        `- work_item_${b2Id} [Success]: item two done`,
        `- work_item_${b3Id} [Success]: item three done`,
      ].join("\n");
      expect(content(t8, "outcome.md")).toBe(
        `# Pursuit outcome\n\n## leg_${iter1Id} [Success]\n${first}\n\n## leg_${iter2Id} [Success]\n${second}`,
      );
    },
  );

  covers(
    ["T7", "dir leg_<id>/", "file goal.md", "file leg_goal.md", "file next_leg_goal.md", "file leg outcome.md"],
    "prior-leg directories, declarations, and outcomes persist readable through promotion and terminal",
    () => {
      for (const point of [t7, t1b, t2b, t8]) {
        expect(hasDir(point, iter1Dir), `${point.label}: leg 1 remains`).toBe(true);
        expect(content(point, `${iter1Dir}/leg_goal.md`), `${point.label}: focus`).toBe(
          "first half",
        );
        expect(
          content(point, `${iter1Dir}/next_leg_goal.md`),
          `${point.label}: deferral`,
        ).toBe("second half");
        expect(
          content(point, `${iter1Dir}/outcome.md`),
          `${point.label}: outcome created at T7 never changes`,
        ).toBe(content(t7, `${iter1Dir}/outcome.md`));
        expect(content(point, "goal.md"), `${point.label}: goal.md persists`).toBe(
          "whole goal",
        );
      }
    },
  );

  covers(
    ["T2", "file work-item summary.md", "file work-item outcome.md", "file attempt outcome.md"],
    "T2 in a promoted leg: one submission renders that item's files while the attempt stays Running",
    () => {
      const b2Dir = `${attempt2Dir}/work_item_${b2Id}`;
      expect(appeared(t1b, t2b)).toEqual([`${b2Dir}/outcome.md`, `${b2Dir}/summary.md`]);
      expect(t2b.tree.legs[1].attempts[0].status).toBe("Running");
      expect(has(t2b, `${attempt2Dir}/outcome.md`), "no attempt outcome mid-attempt").toBe(
        false,
      );
    },
  );

  covers(
    ["T0", "T7", "T8", "dir pursuit_<id>/", "dir leg_<id>/", "dir superseded/"],
    "the pursuit root path never moves and no leg directory is ever superseded",
    () => {
      for (const point of points) {
        expect(point.context.rootPath, `${point.label}: root path`).toBe(t0.context.rootPath);
        expect(hasDir(point, ""), `${point.label}: root directory`).toBe(true);
        expect(
          dirPaths(point).filter((path) => path.split("/").includes("superseded")),
          `${point.label}: promotion archives nothing`,
        ).toEqual([]);
      }
    },
  );

  covers(
    ["T0", "T1", "T2", "T7", "T8", "dir pursuit_<id>/", "dir plan_<id>/ never exists"],
    "the disk mirror equals the in-memory universe at every promotion projection point",
    () => {
      for (const point of points) {
        expectMirrorParity(point);
        expect(planSegments(point.disk.keys()), `${point.label}: no plan dirs on disk`).toEqual(
          [],
        );
      }
    },
  );
});

// ---------------------------------------------------------------------------
// Scenario C - work-item failure, sibling cancellation, keep-retry, budget
// exhaustion. Points: T0, T1, T4/T5 (fail + retry), T1 (keep), T6.
// ---------------------------------------------------------------------------

describe("creation schedule: failure, retry, and budget exhaustion (T4→T5→T6)", () => {
  let t0: Snap, t1: Snap, t45: Snap, t1r: Snap, t6: Snap;
  let points: Snap[];
  let iterDir: string, attempt1Dir: string, attempt2Dir: string;
  let iterId: string, c1Id: string, c2Id: string, c3Id: string;

  beforeAll(async () => {
    const h = harness();
    const wf = await h.delegate("retry goal", 2);
    t0 = await snap(h, wf.pursuitId, "T0");

    await h.launches[0].submitPlanner(
      plannerPayload({
        summary: "planned the direction",
        leg_goal: "the direction",
        next_leg_goal: "phase two",
        work_items: [
          workItem("c1", "item alpha", "spec alpha"),
          workItem("c2", "item beta", "spec beta"),
        ],
      }),
    );
    t1 = await snap(h, wf.pursuitId, "T1");
    const leg = t1.tree.legs[0];
    iterId = leg.id;
    iterDir = `leg_${leg.id}`;
    attempt1Dir = `${iterDir}/attempt_${leg.attempts[0].id}`;
    [c1Id, c2Id] = leg.attempts[0].workItems.map((item) => item.id);

    await workerFor(h, "item alpha").submitWorker(
      workerPayload({ is_pass: false, summary: "broke it", outcome: "failure details" }),
    );
    t45 = await snap(h, wf.pursuitId, "T4/T5");
    attempt2Dir = `${iterDir}/attempt_${t45.tree.legs[0].attempts[1].id}`;

    await h.launches[3].submitPlanner(
      plannerPayload({
        summary: "kept the course",
        leg_goal: undefined,
        next_leg_goal: undefined,
        work_items: [workItem("c3", "item gamma", "spec gamma")],
      }),
    );
    t1r = await snap(h, wf.pursuitId, "T1 (retry keep)");
    c3Id = t1r.tree.legs[0].attempts[1].workItems[0].id;

    await workerFor(h, "item gamma").submitWorker(
      workerPayload({ is_pass: false, summary: "second failure", outcome: "more details" }),
    );
    t6 = await snap(h, wf.pursuitId, "T6");
    points = [t0, t1, t45, t1r, t6];
  });

  covers(
    ["T4", "file work-item summary.md", "file work-item outcome.md", "file failure_reasons.md", "file attempt outcome.md"],
    "T4: a failing submission renders the worker files, failure_reasons.md, and the attempt outcome in one commit",
    () => {
      expect(content(t45, `${attempt1Dir}/work_item_${c1Id}/summary.md`)).toBe("broke it");
      expect(content(t45, `${attempt1Dir}/work_item_${c1Id}/outcome.md`)).toBe(
        "failure details",
      );
      expect(content(t45, `${attempt1Dir}/failure_reasons.md`)).toContain("broke it");
      expect(content(t45, `${attempt1Dir}/outcome.md`), "planner order, cancelled row last").toBe(
        [
          "# Attempt outcome",
          `- work_item_${c1Id} [Failed]: broke it`,
          `- work_item_${c2Id} [Cancelled]: (no summary)`,
        ].join("\n"),
      );
    },
  );

  covers(
    ["T4", "file work-item summary.md", "file work-item outcome.md", "dir work_item_<id>/", "file description.md", "file spec.md"],
    "T4: a cancelled sibling with no submission keeps its directory and static files but gains no summary or outcome",
    () => {
      const c2Dir = `${attempt1Dir}/work_item_${c2Id}`;
      expect(
        t45.tree.legs[0].attempts[0].workItems.find((item) => item.id === c2Id)
          ?.status,
      ).toBe("Cancelled");
      expect(hasDir(t45, c2Dir)).toBe(true);
      expect(content(t45, `${c2Dir}/description.md`)).toBe("item beta");
      expect(content(t45, `${c2Dir}/spec.md`)).toBe("spec beta");
      expect(has(t45, `${c2Dir}/summary.md`), "no summary for the cancelled sibling").toBe(false);
      expect(has(t45, `${c2Dir}/outcome.md`), "no outcome for the cancelled sibling").toBe(false);
    },
  );

  covers(
    ["T5", "dir attempt_<id>/", "file leg outcome.md", "file root outcome.md"],
    "T5: the retry attempt directory appears while the leg stays Running with no outcome files",
    () => {
      expect(t45.tree.legs[0].attempts, "the retry attempt exists").toHaveLength(2);
      expect(hasDir(t45, attempt2Dir), "the retry attempt directory is live").toBe(true);
      expect(t45.tree.legs[0].status).toBe("Running");
      expect(has(t45, `${iterDir}/outcome.md`), "no leg outcome while budget remains").toBe(
        false,
      );
      expect(has(t45, "outcome.md"), "no pursuit outcome while budget remains").toBe(false);
    },
  );

  covers(
    ["T1", "T5", "file leg_goal.md", "file next_leg_goal.md", "file plan_summary.md"],
    "T1 on retry: a keep submission leaves the standing declaration files and renders its own plan_summary.md",
    () => {
      expect(content(t1r, `${iterDir}/leg_goal.md`), "focus unchanged by keep").toBe(
        "the direction",
      );
      expect(content(t1r, `${iterDir}/next_leg_goal.md`), "deferral unchanged by keep").toBe(
        "phase two",
      );
      expect(content(t1r, `${attempt2Dir}/plan_summary.md`)).toBe("kept the course");
      expect(
        content(t1r, `${attempt1Dir}/plan_summary.md`),
        "the failed attempt keeps its own summary",
      ).toBe("planned the direction");
    },
  );

  covers(
    ["T6", "file leg outcome.md", "file attempt outcome.md", "file root outcome.md", "file goal.md"],
    "T6: exhausting the budget renders leg and pursuit outcomes from the failed closing attempt",
    () => {
      expect(t6.tree.pursuit.status).toBe("Failed");
      const closing = `# Attempt outcome\n- work_item_${c3Id} [Failed]: second failure`;
      expect(content(t6, `${attempt2Dir}/outcome.md`)).toBe(closing);
      expect(content(t6, `${iterDir}/outcome.md`), "leg = failed closing attempt").toBe(
        closing,
      );
      expect(content(t6, "outcome.md"), "the ledger includes the failed leg").toBe(
        `# Pursuit outcome\n\n## leg_${iterId} [Failed]\n${closing}`,
      );
      expect(content(t6, "goal.md"), "goal.md survives terminal failure").toBe("retry goal");
    },
  );

  covers(
    ["T6", "file failure_reasons.md", "file attempt outcome.md"],
    "T6: failure_reasons.md stays a separate attempt fact - outcomes embed work-item summaries only",
    () => {
      expect(has(t6, `${attempt1Dir}/failure_reasons.md`), "first failed attempt").toBe(true);
      expect(has(t6, `${attempt2Dir}/failure_reasons.md`), "second failed attempt").toBe(true);
      expect(content(t6, `${attempt2Dir}/failure_reasons.md`)).toContain("second failure");
      expect(
        content(t6, `${attempt2Dir}/outcome.md`),
        "the outcome is exactly the work-item rows, no fail-reason section",
      ).toBe(`# Attempt outcome\n- work_item_${c3Id} [Failed]: second failure`);
      expect(content(t6, `${attempt1Dir}/failure_reasons.md`)).not.toContain("# Attempt outcome");
    },
  );

  covers(
    ["T6", "file next_leg_goal.md", "dir leg_<id>/", "file root outcome.md"],
    "T6: a standing deferral on a failed leg promotes nothing",
    () => {
      expect(t6.tree.legs, "no promoted leg").toHaveLength(1);
      expect(content(t6, `${iterDir}/next_leg_goal.md`), "the deferral file remains").toBe(
        "phase two",
      );
      const ledgerSections = (content(t6, "outcome.md") ?? "")
        .split("\n")
        .filter((line) => line.startsWith("## leg_"));
      expect(ledgerSections, "exactly one leg section").toHaveLength(1);
    },
  );

  covers(
    ["T4", "file description.md", "file spec.md", "dir work_item_<id>/"],
    "T4: failed and cancelled work items keep planner static files through the retry and terminal",
    () => {
      for (const point of [t45, t1r, t6]) {
        for (const [id, description, spec] of [
          [c1Id, "item alpha", "spec alpha"],
          [c2Id, "item beta", "spec beta"],
        ] as const) {
          const dir = `${attempt1Dir}/work_item_${id}`;
          expect(hasDir(point, dir), `${point.label}: ${dir} exists`).toBe(true);
          expect(content(point, `${dir}/description.md`), `${point.label}: description`).toBe(
            description,
          );
          expect(content(point, `${dir}/spec.md`), `${point.label}: spec`).toBe(spec);
        }
      }
    },
  );

  covers(
    ["T0", "T1", "T4", "T5", "T6", "dir pursuit_<id>/", "dir plan_<id>/ never exists"],
    "the disk mirror equals the in-memory universe at every failure-path projection point",
    () => {
      for (const point of points) {
        expectMirrorParity(point);
        expect(planSegments(point.disk.keys()), `${point.label}: no plan dirs on disk`).toEqual(
          [],
        );
      }
    },
  );
});

// ---------------------------------------------------------------------------
// Scenario D - planner death (T1F), retry, second death exhausts the budget.
// ---------------------------------------------------------------------------

describe("creation schedule: planner death synthesis (T1F) through exhaustion (T6)", () => {
  let t1f: Snap, t6: Snap;
  let iterDir: string, attempt1Dir: string, attempt2Dir: string, iterId: string;

  beforeAll(async () => {
    const h = harness();
    const wf = await h.delegate("doomed goal", 2);
    h.launches[0].settle({ status: "failed" });
    await until(() => h.launches.length === 2, "the retry planner launched");
    t1f = await snap(h, wf.pursuitId, "T1F");
    const leg = t1f.tree.legs[0];
    iterId = leg.id;
    iterDir = `leg_${leg.id}`;
    attempt1Dir = `${iterDir}/attempt_${leg.attempts[0].id}`;
    attempt2Dir = `${iterDir}/attempt_${leg.attempts[1].id}`;

    h.launches[1].settle({ status: "failed" });
    await wf.terminal;
    t6 = await snap(h, wf.pursuitId, "T6 (planner deaths)");
  });

  covers(
    ["T1F", "file failure_reasons.md", "file plan_summary.md", "file attempt outcome.md"],
    "T1F: a dead planner renders failure_reasons.md and a '(no work items)' outcome with no plan_summary.md",
    () => {
      expect(t1f.tree.legs[0].attempts[0].status).toBe("Failed");
      expect(content(t1f, `${attempt1Dir}/failure_reasons.md`)).toContain(
        "run settled 'failed' without a submission",
      );
      expect(content(t1f, `${attempt1Dir}/outcome.md`)).toBe(
        "# Attempt outcome\n(no work items)",
      );
      expect(has(t1f, `${attempt1Dir}/plan_summary.md`), "no summary without a submission").toBe(
        false,
      );
    },
  );

  covers(
    ["T1F", "T5", "dir attempt_<id>/"],
    "T1F: the retry attempt directory appears while budget remains and the leg stays Running",
    () => {
      expect(t1f.tree.legs[0].attempts).toHaveLength(2);
      expect(hasDir(t1f, attempt2Dir), "the retry attempt directory is live").toBe(true);
      expect(
        filePaths(t1f).filter((path) => path.startsWith(`${attempt2Dir}/`)),
        "the fresh retry attempt owns no files yet",
      ).toEqual([]);
      expect(t1f.tree.legs[0].status).toBe("Running");
    },
  );

  covers(
    ["T1F", "dir work_item_<id>/", "file leg_goal.md", "file next_leg_goal.md"],
    "T1F: no work-item directories or declaration files exist after a planner death",
    () => {
      expect(
        dirPaths(t1f).filter((path) => path.includes("work_item_")),
        "a dead planner materialized nothing",
      ).toEqual([]);
      expect(has(t1f, `${iterDir}/leg_goal.md`), "no focus was ever declared").toBe(false);
      expect(has(t1f, `${iterDir}/next_leg_goal.md`), "no deferral was ever declared").toBe(
        false,
      );
    },
  );

  covers(
    ["T1F", "T6", "file leg outcome.md", "file root outcome.md", "file attempt outcome.md"],
    "T6 via planner deaths: the '(no work items)' closing outcome propagates to leg and pursuit",
    () => {
      expect(t6.tree.pursuit.status).toBe("Failed");
      expect(content(t6, `${attempt2Dir}/outcome.md`)).toBe(
        "# Attempt outcome\n(no work items)",
      );
      expect(content(t6, `${iterDir}/outcome.md`)).toBe("# Attempt outcome\n(no work items)");
      expect(content(t6, "outcome.md")).toBe(
        `# Pursuit outcome\n\n## leg_${iterId} [Failed]\n# Attempt outcome\n(no work items)`,
      );
    },
  );

  covers(
    ["T1F", "T6", "dir pursuit_<id>/", "dir plan_<id>/ never exists", "file goal.md"],
    "the disk mirror equals the in-memory universe after each planner-death projection",
    () => {
      for (const point of [t1f, t6]) {
        expectMirrorParity(point);
        expect(content(point, "goal.md"), `${point.label}: goal.md persists`).toBe(
          "doomed goal",
        );
        expect(planSegments(point.disk.keys()), `${point.label}: no plan dirs on disk`).toEqual(
          [],
        );
      }
    },
  );
});

// ---------------------------------------------------------------------------
// Scenario E - context composition failure (the other T1F arm): the launch
// never happens and the ordinary retry path bounds it.
// ---------------------------------------------------------------------------

describe("creation schedule: compose failure synthesis (T1F) without any launch", () => {
  let end: Snap;
  let launches: number;
  let iterDir: string, iterId: string;
  let attemptDirs: string[];

  beforeAll(async () => {
    const h = harness({ compose: () => Promise.reject(new Error("boom")) });
    const wf = await h.delegate("never launches", 2);
    await wf.terminal;
    launches = h.launches.length;
    end = await snap(h, wf.pursuitId, "T6 (compose failures)");
    const leg = end.tree.legs[0];
    iterId = leg.id;
    iterDir = `leg_${leg.id}`;
    attemptDirs = leg.attempts.map(
      (attempt) => `${iterDir}/attempt_${attempt.id}`,
    );
  });

  covers(
    ["T1F", "file failure_reasons.md", "file attempt outcome.md", "file plan_summary.md"],
    "T1F: a compose failure synthesizes context_script_error fail reasons and '(no work items)' outcomes with no launch",
    () => {
      expect(launches, "the launch never happens on compose failure").toBe(0);
      expect(end.tree.legs[0].attempts).toHaveLength(2);
      for (const dir of attemptDirs) {
        expect(content(end, `${dir}/failure_reasons.md`), `${dir} fail reason`).toContain(
          "context_script_error: boom",
        );
        expect(content(end, `${dir}/outcome.md`), `${dir} outcome`).toBe(
          "# Attempt outcome\n(no work items)",
        );
        expect(has(end, `${dir}/plan_summary.md`), `${dir} has no plan summary`).toBe(false);
      }
    },
  );

  covers(
    ["T1F", "T6", "file leg outcome.md", "file root outcome.md", "dir work_item_<id>/", "dir attempt_<id>/"],
    "T6 via compose failures: budget exhaustion renders leg and pursuit outcomes with no work items anywhere",
    () => {
      expect(end.tree.pursuit.status).toBe("Failed");
      for (const dir of attemptDirs) {
        expect(hasDir(end, dir), `${dir} stays a live attempt directory`).toBe(true);
      }
      expect(
        dirPaths(end).filter((path) => path.includes("work_item_")),
        "nothing materialized",
      ).toEqual([]);
      expect(content(end, `${iterDir}/outcome.md`)).toBe(
        "# Attempt outcome\n(no work items)",
      );
      expect(content(end, "outcome.md")).toBe(
        `# Pursuit outcome\n\n## leg_${iterId} [Failed]\n# Attempt outcome\n(no work items)`,
      );
    },
  );
});

// ---------------------------------------------------------------------------
// Scenario F - refocus relocation (T9): declare → fail → keep → fail →
// refocus → succeed. The declarer and the keeper both drift; only the
// declarer carries the superseded declaration files.
// ---------------------------------------------------------------------------

describe("creation schedule: refocus relocation (T9) and recovery (T8)", () => {
  let t0: Snap, t1: Snap, t45a: Snap, t1k: Snap, t45b: Snap, t9: Snap, t8: Snap;
  let points: Snap[];
  let iterDir: string;
  let liveA1: string, liveA2: string, liveA3: string;
  let archA1: string, archA2: string;
  let iterId: string, f1Id: string, f3Id: string;

  beforeAll(async () => {
    const h = harness();
    const wf = await h.delegate("recover the goal", 4);
    t0 = await snap(h, wf.pursuitId, "T0");

    await h.launches[0].submitPlanner(
      plannerPayload({
        summary: "planned first direction",
        leg_goal: "first direction",
        next_leg_goal: "left for later",
        work_items: [workItem("f1", "recover item one", "spec one")],
      }),
    );
    t1 = await snap(h, wf.pursuitId, "T1");
    const leg = t1.tree.legs[0];
    iterId = leg.id;
    iterDir = `leg_${leg.id}`;
    const a1 = leg.attempts[0].id;
    liveA1 = `${iterDir}/attempt_${a1}`;
    archA1 = `${iterDir}/superseded/attempt_${a1}`;
    f1Id = leg.attempts[0].workItems[0].id;

    await workerFor(h, "recover item one").submitWorker(
      workerPayload({ is_pass: false, summary: "dead end", outcome: "hit a wall" }),
    );
    t45a = await snap(h, wf.pursuitId, "T4/T5 (first failure)");
    const a2 = t45a.tree.legs[0].attempts[1].id;
    liveA2 = `${iterDir}/attempt_${a2}`;
    archA2 = `${iterDir}/superseded/attempt_${a2}`;

    await h.launches[2].submitPlanner(
      plannerPayload({
        summary: "kept the course",
        leg_goal: undefined,
        next_leg_goal: undefined,
        work_items: [workItem("f2", "recover item two", "spec two")],
      }),
    );
    t1k = await snap(h, wf.pursuitId, "T1 (keep)");

    await workerFor(h, "recover item two").submitWorker(
      workerPayload({ is_pass: false, summary: "still stuck", outcome: "wall again" }),
    );
    t45b = await snap(h, wf.pursuitId, "T4/T5 (second failure)");
    const a3 = t45b.tree.legs[0].attempts[2].id;
    liveA3 = `${iterDir}/attempt_${a3}`;

    await h.launches[4].submitPlanner(
      plannerPayload({
        summary: "planned second direction",
        leg_goal: "second direction",
        work_items: [workItem("f3", "recover item three", "spec three")],
      }),
    );
    t9 = await snap(h, wf.pursuitId, "T9");
    f3Id = t9.tree.legs[0].attempts[2].workItems[0].id;

    await workerFor(h, "recover item three").submitWorker(
      workerPayload({ summary: "recovered", outcome: "fixed it" }),
    );
    t8 = await snap(h, wf.pursuitId, "T8 (after refocus)");
    points = [t0, t1, t45a, t1k, t45b, t9, t8];
  });

  covers(
    ["T9", "dir superseded/", "dir superseded/attempt_<id>/", "dir attempt_<id>/"],
    "T9: a refocus relocates every superseded attempt from its live path to superseded/attempt_<id>/",
    () => {
      expect(hasDir(t45b, `${iterDir}/superseded`), "no archive before the refocus").toBe(false);
      expect(hasDir(t9, `${iterDir}/superseded`), "superseded/ first appears at T9").toBe(true);
      expect(hasDir(t9, archA1), "the declarer relocated").toBe(true);
      expect(hasDir(t9, archA2), "the keeper relocated").toBe(true);
      expect(hasDir(t9, liveA1), "the declarer's live path disappeared").toBe(false);
      expect(hasDir(t9, liveA2), "the keeper's live path disappeared").toBe(false);
      expect(hasDir(t9, liveA3), "the refocusing attempt stays live").toBe(true);
    },
  );

  covers(
    ["T9", "file superseded leg_goal.md", "file superseded next_leg_goal.md"],
    "T9: the declaring attempt's superseded folder carries the superseded leg_goal.md and next_leg_goal.md verbatim",
    () => {
      expect(content(t9, `${archA1}/leg_goal.md`)).toBe("first direction");
      expect(content(t9, `${archA1}/next_leg_goal.md`)).toBe("left for later");
    },
  );

  covers(
    ["T9", "file superseded leg_goal.md", "file superseded next_leg_goal.md", "dir superseded/attempt_<id>/"],
    "T9: a keep attempt archives without declaration files - they ride only the declarer",
    () => {
      expect(has(t9, `${archA2}/leg_goal.md`), "the keeper declared no focus").toBe(false);
      expect(has(t9, `${archA2}/next_leg_goal.md`), "the keeper declared no deferral").toBe(
        false,
      );
      expect(content(t9, `${archA2}/plan_summary.md`), "attempt-owned files still ride it").toBe(
        "kept the course",
      );
    },
  );

  covers(
    ["T9", "file plan_summary.md", "file failure_reasons.md", "file attempt outcome.md", "file description.md", "file spec.md", "file work-item summary.md", "file work-item outcome.md", "dir work_item_<id>/"],
    "T9: every attempt-owned and work-item file moves whole, byte-identical, with the relocated attempt",
    () => {
      const moved = filePaths(t45b).filter((path) => path.startsWith(`${liveA1}/`));
      expect(moved, "the drifted attempt owned files before the refocus").toEqual([
        `${liveA1}/failure_reasons.md`,
        `${liveA1}/outcome.md`,
        `${liveA1}/plan_summary.md`,
        `${liveA1}/work_item_${f1Id}/description.md`,
        `${liveA1}/work_item_${f1Id}/outcome.md`,
        `${liveA1}/work_item_${f1Id}/spec.md`,
        `${liveA1}/work_item_${f1Id}/summary.md`,
      ]);
      for (const livePath of moved) {
        const suffix = livePath.slice(liveA1.length + 1);
        expect(has(t9, livePath), `${livePath} left the live universe`).toBe(false);
        expect(
          content(t9, `${archA1}/${suffix}`),
          `${suffix} moved byte-identical`,
        ).toBe(content(t45b, livePath));
      }
      expect(hasDir(t9, `${archA1}/work_item_${f1Id}`), "the work-item dir moved too").toBe(
        true,
      );
      expect(hasDir(t9, `${liveA1}/work_item_${f1Id}`)).toBe(false);
    },
  );

  covers(
    ["T9", "file leg_goal.md", "file next_leg_goal.md"],
    "T9: the leg declaration files are replaced - leg_goal.md updates and the omitted deferral disappears",
    () => {
      expect(content(t45b, `${iterDir}/leg_goal.md`), "before the refocus").toBe("first direction");
      expect(content(t45b, `${iterDir}/next_leg_goal.md`), "before the refocus").toBe(
        "left for later",
      );
      expect(content(t9, `${iterDir}/leg_goal.md`), "replaced at T9").toBe("second direction");
      expect(has(t9, `${iterDir}/next_leg_goal.md`), "removed at T9 when omitted").toBe(false);
    },
  );

  covers(
    ["T9", "dir attempt_<id>/", "dir superseded/attempt_<id>/"],
    "live attempt paths exist iff the attempt is consistent with the leg focus",
    () => {
      for (const point of [t45b, t9]) {
        for (const attempt of point.tree.legs[0].attempts) {
          expect(
            hasDir(point, `${iterDir}/attempt_${attempt.id}`),
            `${point.label}: live path of attempt ${attempt.id}`,
          ).toBe(attempt.isConsistentWithLegGoal);
          expect(
            hasDir(point, `${iterDir}/superseded/attempt_${attempt.id}`),
            `${point.label}: superseded path of attempt ${attempt.id}`,
          ).toBe(!attempt.isConsistentWithLegGoal);
        }
      }
    },
  );

  covers(
    ["T3", "T8", "file leg outcome.md", "file attempt outcome.md", "file root outcome.md"],
    "T8 after a refocus: outcomes derive from the live closing attempt only",
    () => {
      expect(t8.tree.pursuit.status).toBe("Success");
      const closing = `# Attempt outcome\n- work_item_${f3Id} [Success]: recovered`;
      expect(content(t8, `${liveA3}/outcome.md`)).toBe(closing);
      expect(content(t8, `${iterDir}/outcome.md`)).toBe(closing);
      expect(content(t8, "outcome.md")).toBe(
        `# Pursuit outcome\n\n## leg_${iterId} [Success]\n${closing}`,
      );
      expect(content(t8, "outcome.md"), "superseded failures stay out").not.toContain("dead end");
      expect(content(t8, "outcome.md"), "superseded failures stay out").not.toContain(
        "still stuck",
      );
    },
  );

  covers(
    ["T9", "dir superseded/", "dir superseded/attempt_<id>/", "file superseded leg_goal.md", "file superseded next_leg_goal.md"],
    "superseded attempts and their files persist unchanged through later projection points",
    () => {
      const supersededPaths = filePaths(t9).filter((path) =>
        path.startsWith(`${iterDir}/superseded/`),
      );
      expect(supersededPaths.length, "the archive is not empty").toBeGreaterThan(0);
      for (const path of supersededPaths) {
        expect(content(t8, path), `${path} survives to terminal unchanged`).toBe(
          content(t9, path),
        );
      }
      expect(hasDir(t8, `${iterDir}/superseded`)).toBe(true);
    },
  );

  covers(
    ["T9", "file superseded leg_goal.md", "file superseded next_leg_goal.md", "file leg_goal.md", "file next_leg_goal.md"],
    "T9: the superseded declaration preserves exactly the leg files the refocus replaced",
    () => {
      expect(
        content(t9, `${archA1}/leg_goal.md`),
        "the superseded focus is the pre-refocus leg focus",
      ).toBe(content(t45b, `${iterDir}/leg_goal.md`));
      expect(
        content(t9, `${archA1}/next_leg_goal.md`),
        "the superseded deferral is the pre-refocus leg deferral",
      ).toBe(content(t45b, `${iterDir}/next_leg_goal.md`));
      expect(
        content(t9, `${archA1}/leg_goal.md`),
        "superseded and live focus differ",
      ).not.toBe(content(t9, `${iterDir}/leg_goal.md`));
    },
  );

  covers(
    ["T4", "T5", "dir attempt_<id>/", "file failure_reasons.md"],
    "each failure with budget left appends a live retry attempt before any refocus",
    () => {
      expect(t45a.tree.legs[0].attempts, "first retry appended").toHaveLength(2);
      expect(hasDir(t45a, liveA2), "the first retry attempt is live").toBe(true);
      expect(has(t45a, `${liveA1}/failure_reasons.md`), "the first failure recorded").toBe(true);
      expect(t45b.tree.legs[0].attempts, "second retry appended").toHaveLength(3);
      expect(hasDir(t45b, liveA3), "the second retry attempt is live").toBe(true);
      expect(
        t45b.tree.legs[0].attempts.every(
          (attempt) => attempt.isConsistentWithLegGoal,
        ),
        "keep retries drift nothing",
      ).toBe(true);
    },
  );

  covers(
    ["T0", "T1", "T4", "T5", "T9", "T8", "dir pursuit_<id>/", "dir plan_<id>/ never exists", "dir superseded/", "dir superseded/attempt_<id>/", "file superseded leg_goal.md", "file superseded next_leg_goal.md"],
    "the disk mirror equals the in-memory universe at every refocus projection point, superseded paths included",
    () => {
      for (const point of points) expectMirrorParity(point);
      expect(t9.disk.get(`${archA1}/leg_goal.md`), "superseded focus on disk").toBe(
        "first direction",
      );
      expect(t9.disk.get(`${archA1}/next_leg_goal.md`), "superseded deferral on disk").toBe(
        "left for later",
      );
      expect(t9.disk.has(`${liveA1}/plan_summary.md`), "old live path pruned on disk").toBe(
        false,
      );
    },
  );
});

// ---------------------------------------------------------------------------
// Scenario G - cancellation (T10) after one closed leg: status, not
// business outcome.
// ---------------------------------------------------------------------------

describe("creation schedule: cancellation marker semantics (T10)", () => {
  let t0: Snap, t1: Snap, t7: Snap, t1b: Snap, t10: Snap;
  let points: Snap[];
  let iter1Dir: string, iter2Dir: string, attempt1Dir: string, attempt2Dir: string;
  let iter1Id: string, g1Id: string, g2Id: string;

  beforeAll(async () => {
    const h = harness();
    const wf = await h.delegate("whole goal");
    t0 = await snap(h, wf.pursuitId, "T0");

    await h.launches[0].submitPlanner(
      plannerPayload({
        summary: "planned first half",
        leg_goal: "first half",
        next_leg_goal: "second half",
        work_items: [workItem("g1", "cancel item one", "spec g1")],
      }),
    );
    t1 = await snap(h, wf.pursuitId, "T1");
    const iter1 = t1.tree.legs[0];
    iter1Id = iter1.id;
    iter1Dir = `leg_${iter1.id}`;
    attempt1Dir = `${iter1Dir}/attempt_${iter1.attempts[0].id}`;
    g1Id = iter1.attempts[0].workItems[0].id;

    await workerFor(h, "cancel item one").submitWorker(
      workerPayload({ summary: "first half done", outcome: "one details" }),
    );
    t7 = await snap(h, wf.pursuitId, "T7");

    await h.launches[2].submitPlanner(
      plannerPayload({
        summary: "planned second half",
        leg_goal: "second focus",
        work_items: [workItem("g2", "cancel item two", "spec g2")],
      }),
    );
    t1b = await snap(h, wf.pursuitId, "T1 (second leg)");
    const iter2 = t1b.tree.legs[1];
    iter2Dir = `leg_${iter2.id}`;
    attempt2Dir = `${iter2Dir}/attempt_${iter2.attempts[0].id}`;
    g2Id = iter2.attempts[0].workItems[0].id;

    await wf.cancel("changed direction");
    t10 = await snap(h, wf.pursuitId, "T10");
    points = [t0, t1, t7, t1b, t10];
  });

  covers(
    ["T10", "file root outcome.md"],
    "T10: a cancelled pursuit renders the cancellation marker followed by already-closed leg outcomes",
    () => {
      expect(t10.tree.pursuit.status).toBe("Cancelled");
      expect(content(t10, "outcome.md")).toBe(
        `# Pursuit outcome\npursuit cancelled\n\n## leg_${iter1Id} [Success]\n# Attempt outcome\n- work_item_${g1Id} [Success]: first half done`,
      );
    },
  );

  covers(
    ["T10", "file leg outcome.md", "file attempt outcome.md"],
    "T10: cancelled legs and attempts gain no business outcome.md",
    () => {
      expect(t10.tree.legs[1].status).toBe("Cancelled");
      expect(t10.tree.legs[1].attempts[0].status).toBe("Cancelled");
      expect(has(t10, `${iter2Dir}/outcome.md`), "no cancelled-leg outcome").toBe(false);
      expect(has(t10, `${attempt2Dir}/outcome.md`), "no cancelled-attempt outcome").toBe(false);
    },
  );

  covers(
    ["T10", "file work-item summary.md", "file work-item outcome.md", "dir work_item_<id>/", "file description.md", "file spec.md"],
    "T10: a cancelled work item with no submission keeps static files only",
    () => {
      const g2Dir = `${attempt2Dir}/work_item_${g2Id}`;
      expect(t10.tree.legs[1].attempts[0].workItems[0].status).toBe("Cancelled");
      expect(hasDir(t10, g2Dir)).toBe(true);
      expect(content(t10, `${g2Dir}/description.md`)).toBe("cancel item two");
      expect(content(t10, `${g2Dir}/spec.md`)).toBe("spec g2");
      expect(has(t10, `${g2Dir}/summary.md`), "no summary without a submission").toBe(false);
      expect(has(t10, `${g2Dir}/outcome.md`), "no outcome without a submission").toBe(false);
    },
  );

  covers(
    ["T7", "T10", "file leg outcome.md", "file goal.md", "file leg_goal.md", "file next_leg_goal.md"],
    "T10: closed-leg outcomes and declarations survive cancellation unchanged",
    () => {
      expect(content(t10, `${iter1Dir}/outcome.md`), "the T7 outcome is untouched").toBe(
        content(t7, `${iter1Dir}/outcome.md`),
      );
      expect(content(t10, `${iter1Dir}/leg_goal.md`)).toBe("first half");
      expect(content(t10, `${iter1Dir}/next_leg_goal.md`)).toBe("second half");
      expect(content(t10, `${iter2Dir}/leg_goal.md`)).toBe("second focus");
      expect(content(t10, "goal.md")).toBe("whole goal");
    },
  );

  covers(
    ["T10", "file plan_summary.md", "file failure_reasons.md"],
    "T10: cancelled attempts keep plan_summary.md and gain no failure_reasons.md",
    () => {
      expect(content(t10, `${attempt2Dir}/plan_summary.md`)).toBe("planned second half");
      expect(has(t10, `${attempt2Dir}/failure_reasons.md`), "cancelled is not failed").toBe(false);
      expect(content(t10, `${attempt1Dir}/plan_summary.md`), "closed attempts keep theirs").toBe(
        "planned first half",
      );
    },
  );

  covers(
    ["T10", "dir pursuit_<id>/", "dir leg_<id>/", "dir attempt_<id>/", "dir work_item_<id>/", "dir superseded/"],
    "T10: cancellation removes no directories and archives nothing",
    () => {
      expect(dirPaths(t10), "the directory set is unchanged by the cancel").toEqual(
        dirPaths(t1b),
      );
      expect(
        dirPaths(t10).filter((path) => path.split("/").includes("superseded")),
        "cancellation archives nothing",
      ).toEqual([]);
    },
  );

  covers(
    ["T0", "T1", "T7", "T10", "dir pursuit_<id>/", "dir plan_<id>/ never exists"],
    "the disk mirror equals the in-memory universe at every cancellation projection point",
    () => {
      for (const point of points) {
        expectMirrorParity(point);
        expect(planSegments(point.disk.keys()), `${point.label}: no plan dirs on disk`).toEqual(
          [],
        );
      }
    },
  );
});

// ---------------------------------------------------------------------------
// Scenario H - worker death synthesis on a one-attempt budget: the §8
// "synthesized failure reason" content source for summary.md/outcome.md.
// ---------------------------------------------------------------------------

describe("creation schedule: worker death synthesis (T4) on an exhausted budget (T6)", () => {
  let t1: Snap, t6: Snap;
  let iterDir: string, attemptDir: string, iterId: string, h1Id: string;

  beforeAll(async () => {
    const h = harness();
    const wf = await h.delegate("fragile goal", 1);
    await h.launches[0].submitPlanner(
      plannerPayload({
        summary: "planned the try",
        leg_goal: "the only try",
        work_items: [workItem("h1", "fragile item", "spec h")],
      }),
    );
    t1 = await snap(h, wf.pursuitId, "T1");
    const leg = t1.tree.legs[0];
    iterId = leg.id;
    iterDir = `leg_${leg.id}`;
    attemptDir = `${iterDir}/attempt_${leg.attempts[0].id}`;
    h1Id = leg.attempts[0].workItems[0].id;

    workerFor(h, "fragile item").settle({ status: "failed" });
    await wf.terminal;
    t6 = await snap(h, wf.pursuitId, "T6 (worker death)");
  });

  covers(
    ["T4", "file work-item summary.md", "file work-item outcome.md"],
    "T4 via worker death: summary.md and outcome.md synthesize the failure reason",
    () => {
      const reason = "run settled 'failed' without a submission";
      expect(t6.tree.legs[0].attempts[0].workItems[0].status).toBe("Failed");
      expect(content(t6, `${attemptDir}/work_item_${h1Id}/summary.md`)).toContain(reason);
      expect(content(t6, `${attemptDir}/work_item_${h1Id}/outcome.md`)).toContain(reason);
      expect(has(t1, `${attemptDir}/work_item_${h1Id}/summary.md`), "absent before death").toBe(
        false,
      );
    },
  );

  covers(
    ["T4", "T6", "file failure_reasons.md", "file attempt outcome.md", "file leg outcome.md", "file root outcome.md"],
    "T4→T6: the synthesized failure flows through attempt, leg, and pursuit outcomes",
    () => {
      expect(t6.tree.pursuit.status).toBe("Failed");
      const closing = `# Attempt outcome\n- work_item_${h1Id} [Failed]: run settled 'failed' without a submission`;
      expect(content(t6, `${attemptDir}/outcome.md`)).toBe(closing);
      expect(content(t6, `${iterDir}/outcome.md`)).toBe(closing);
      expect(content(t6, "outcome.md")).toBe(
        `# Pursuit outcome\n\n## leg_${iterId} [Failed]\n${closing}`,
      );
      expect(has(t6, `${attemptDir}/failure_reasons.md`), "the attempt records its failure").toBe(
        true,
      );
    },
  );

  covers(
    ["T4", "T6", "dir plan_<id>/ never exists", "dir pursuit_<id>/", "file plan_summary.md"],
    "the disk mirror equals the in-memory universe after death synthesis, plan_summary.md intact",
    () => {
      for (const point of [t1, t6]) {
        expectMirrorParity(point);
        expect(planSegments(point.disk.keys()), `${point.label}: no plan dirs on disk`).toEqual(
          [],
        );
      }
      expect(
        content(t6, `${attemptDir}/plan_summary.md`),
        "the accepted planner summary survives the worker death",
      ).toBe("planned the try");
    },
  );
});

// ---------------------------------------------------------------------------
// §6-§8 coverage matrix: every spec case must be supported by >= 5 tests.
// ---------------------------------------------------------------------------

describe("§6-§8 coverage matrix", () => {
  it.each([...SPEC_CASES])("case '%s' is supported by at least five tests", (specCase) => {
    const titles = support.get(specCase) ?? [];
    expect(
      titles.length,
      `supporting tests for "${specCase}":\n- ${titles.join("\n- ")}`,
    ).toBeGreaterThanOrEqual(5);
  });
});
