import { workItemIdFrom, type PursuitId } from "@eos/contracts";
import {
  loadPursuitRows,
  type AttemptRow,
  type PlanRow,
  type PursuitDbReader,
  type PursuitRows,
  type WorkItemRow,
} from "@eos/db";

import type { AttemptState } from "./attempt/state.js";
import type { LegState } from "./leg/state.js";
import type { PlanState } from "./plan/state.js";
import type { PursuitState } from "./pursuit/state.js";
import type { WorkItemState } from "./work-item/state.js";

export interface PursuitTree {
  readonly pursuit: PursuitState;
  readonly legs: readonly LegState[];
}

export async function loadPursuitTree(
  db: PursuitDbReader,
  pursuitId: PursuitId,
): Promise<PursuitTree | null> {
  const rows = await loadPursuitRows(db, pursuitId);
  return rows ? buildPursuitTree(rows) : null;
}

function buildPursuitTree(rows: PursuitRows): PursuitTree {
  const legs = rows.legs.map((leg) =>
    buildLeg(
      leg,
      rows.attempts.filter((attempt) => attempt.leg_id === leg.id),
      rows.plans,
      rows.workItems,
    ),
  );

  const pursuit: PursuitState = Object.freeze({
    id: rows.pursuit.id,
    parentRunId: rows.pursuit.parent_run_id,
    pursuitGoal: rows.pursuit.pursuit_goal,
    legGoalMode: rows.pursuit.leg_goal_mode,
    legGoals: Object.freeze(decodeStringList(rows.pursuit.leg_goals ?? "[]")),
    status: rows.pursuit.status,
    closedAt: rows.pursuit.closed_at,
  });

  return Object.freeze({ pursuit, legs: Object.freeze(legs) });
}

function buildLeg(
  leg: PursuitRows["legs"][number],
  attemptRows: AttemptRow[],
  planRows: PlanRow[],
  workItemRows: WorkItemRow[],
): LegState {
  const attempts: AttemptState[] = attemptRows.map((attempt) => {
    const plan = buildPlan(requirePlan(planRows, attempt));
    const workItems = workItemRows
      .filter((item) => item.attempt_id === attempt.id)
      .map(buildWorkItem);
    return Object.freeze({
      id: attempt.id,
      sequence: attempt.sequence,
      status: attempt.status,
      failureReasons: Object.freeze(decodeStringList(attempt.failure_reasons)),
      legGoalVersion: attempt.leg_goal_version,
      isConsistentWithLegGoal: attempt.leg_goal_version === leg.leg_goal_version,
      plan,
      workItems: Object.freeze(workItems),
    });
  });

  return Object.freeze({
    id: leg.id,
    sequence: leg.sequence,
    origin: leg.origin,
    maxAttempts: leg.max_attempts,
    status: leg.status,
    legGoal: leg.leg_goal,
    legGoalVersion: leg.leg_goal_version,
    legGoalProvenance: leg.leg_goal_provenance,
    isLegGoalMutatable: leg.is_leg_goal_mutatable === 1,
    nextLegGoal: leg.next_leg_goal,
    attempts: Object.freeze(attempts),
  });
}

function requirePlan(plans: PlanRow[], attempt: AttemptRow): PlanRow {
  const plan = plans.find((row) => row.attempt_id === attempt.id);
  if (!plan) {
    throw new Error(`attempt ${attempt.id} has no plan row`);
  }
  return plan;
}

function buildPlan(row: PlanRow): PlanState {
  return Object.freeze({
    id: row.id,
    attemptId: row.attempt_id,
    agentRunId: row.agent_run_id,
    status: row.status,
    declaredLegGoal: row.declared_leg_goal,
    declaredNextLegGoal: row.declared_next_leg_goal,
    legGoalVersion: row.leg_goal_version,
    summary: row.planner_summary,
  });
}

function buildWorkItem(row: WorkItemRow): WorkItemState {
  return Object.freeze({
    id: row.id,
    planId: row.plan_id,
    agentName: row.agent_name,
    agentRunId: row.agent_run_id,
    status: row.status,
    title: row.title,
    spec: row.spec,
    dependsOn: Object.freeze(decodeDependsOn(row.depends_on)),
    legGoalVersion: row.leg_goal_version,
    summary: row.worker_summary,
    outcome: row.worker_outcome,
  });
}

export function encodeStringList(values: readonly string[]): string {
  return JSON.stringify([...values]);
}

export function encodeDependsOn(dependsOn: readonly string[]): string {
  return encodeStringList(dependsOn);
}

export function decodeStringList(raw: string): string[] {
  const parsed: unknown = JSON.parse(raw);
  if (!Array.isArray(parsed) || parsed.some((value) => typeof value !== "string")) {
    throw new Error(`expected JSON string array: ${raw}`);
  }
  return parsed.map((value) => String(value));
}

function decodeDependsOn(raw: string): WorkItemState["dependsOn"][number][] {
  return decodeStringList(raw).map((id) => workItemIdFrom(id));
}
