import type {
  AttemptId,
  LegId,
  PlanId,
  PlannerContextInput,
  WorkItemId,
  WorkerContextInput,
  PursuitContextAttempt,
  PursuitContextLeg,
  PursuitContextSnapshot,
} from "@eos/contracts";

import { attemptDirPath, legDirName, pursuitRootPath } from "../archive/paths.js";
import type { AttemptState } from "../attempt/state.js";
import type { LegState } from "../leg/state.js";
import type { PursuitTree } from "../pursuit-tree.js";

export interface PlannerLaunchLocator {
  legId: LegId;
  attemptId: AttemptId;
  planId: PlanId;
}

export interface WorkerLaunchLocator {
  legId: LegId;
  attemptId: AttemptId;
  workItemId: WorkItemId;
}

/**
 * Pure §7 script-input builders over the `PursuitTree`: the full
 * serialized `pursuit_context` snapshot plus the current launch locator.
 * No convenience variables are precomputed - the composer (default policy
 * or user script) derives its own locals from `pursuit_context`.
 */
export function buildPlannerContextInput(
  tree: PursuitTree,
  locator: PlannerLaunchLocator,
): PlannerContextInput {
  return {
    kind: "planner",
    pursuit_context: snapshotPursuitContext(tree),
    current: {
      pursuit_id: tree.pursuit.id,
      leg_id: locator.legId,
      attempt_id: locator.attemptId,
      plan_id: locator.planId,
    },
  };
}

export function buildWorkerContextInput(
  tree: PursuitTree,
  locator: WorkerLaunchLocator,
): WorkerContextInput {
  return {
    kind: "worker",
    pursuit_context: snapshotPursuitContext(tree),
    current: {
      pursuit_id: tree.pursuit.id,
      leg_id: locator.legId,
      attempt_id: locator.attemptId,
      work_item_id: locator.workItemId,
    },
  };
}

/** ALL facts, including ones the default policy hides - hiding is policy. */
export function snapshotPursuitContext(tree: PursuitTree): PursuitContextSnapshot {
  const root = pursuitRootPath(tree.pursuit.id);
  return {
    pursuit: {
      id: tree.pursuit.id,
      goal: tree.pursuit.goal,
      status: tree.pursuit.status,
      context_path: root,
      legs: tree.legs.map((leg) =>
        snapshotLeg(root, leg),
      ),
    },
  };
}

function snapshotLeg(
  root: string,
  leg: LegState,
): PursuitContextLeg {
  return {
    id: leg.id,
    sequence: leg.sequence,
    origin: leg.origin,
    status: leg.status,
    focus: leg.focus,
    next_leg_goal: leg.nextLegGoal,
    max_attempts: leg.maxAttempts,
    context_path: `${root}/${legDirName(leg.id)}`,
    attempts: leg.attempts.map((attempt) =>
      snapshotAttempt(root, leg, attempt),
    ),
  };
}

function snapshotAttempt(
  root: string,
  leg: LegState,
  attempt: AttemptState,
): PursuitContextAttempt {
  const attemptPath = `${root}/${attemptDirPath(leg, attempt)}`;
  return {
    id: attempt.id,
    sequence: attempt.sequence,
    status: attempt.status,
    failure_reasons: attempt.failureReasons,
    is_consistent_with_leg_goal: attempt.isConsistentWithLegGoal,
    context_path: attemptPath,
    // No plan context_path: the rendered planner summary is the
    // attempt-owned `${attemptPath}/plan_summary.md` (§2.7).
    plan: {
      id: attempt.plan.id,
      status: attempt.plan.status,
      declared_leg_goal: attempt.plan.declaredLegGoal,
      declared_next_leg_goal: attempt.plan.declaredNextLegGoal,
      summary: attempt.plan.summary,
      agent_run_id: attempt.plan.agentRunId,
    },
    work_items: attempt.workItems.map((item) => ({
      id: item.id,
      agent_name: item.agentName,
      description: item.description,
      spec: item.spec,
      needs: [...item.needs],
      status: item.status,
      summary: item.summary,
      outcome: item.outcome,
      agent_run_id: item.agentRunId,
      context_path: `${attemptPath}/work_item_${item.id}`,
    })),
  };
}
