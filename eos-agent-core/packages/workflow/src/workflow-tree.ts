import { workItemIdFrom, type WorkflowId } from "@eos/contracts";
import {
  loadWorkflowRows,
  type AttemptRow,
  type PlanRow,
  type WorkItemRow,
  type WorkflowDbReader,
  type WorkflowRows,
} from "@eos/db";

import { consistentWithIterationFocus, type AttemptState } from "./attempt/state.js";
import { orderedDeclarations, type IterationState } from "./iteration/state.js";
import type { PlanState } from "./plan/state.js";
import type { WorkItemState } from "./work-item/state.js";
import { deriveGoalChain, type WorkflowState } from "./workflow/state.js";

/**
 * The complete UI/render graph: one frozen
 * `Workflow -> Iteration[] -> Attempt[] -> Plan + WorkItem[]` value with
 * every §8 derived view computed once. Renderers, launch-variable builders,
 * and DTO builders consume this shape and never re-derive.
 */
export interface WorkflowTree {
  readonly workflow: WorkflowState;
  readonly iterations: readonly IterationState[];
}

export async function loadWorkflowTree(
  db: WorkflowDbReader,
  workflowId: WorkflowId,
): Promise<WorkflowTree | null> {
  const rows = await loadWorkflowRows(db, workflowId);
  return rows ? buildWorkflowTree(rows) : null;
}

/** Pure derivation over one row load; exported for tests and synthesis. */
export function buildWorkflowTree(rows: WorkflowRows): WorkflowTree {
  const iterations = rows.iterations.map((iteration) =>
    buildIteration(
      iteration,
      rows.attempts.filter((attempt) => attempt.iteration_id === iteration.id),
      rows.plans,
      rows.workItems,
    ),
  );

  // Goal chain: original for the first iteration, then each predecessor's
  // effective deferral; current_goal is the latest iteration's goal.
  const goals = deriveGoalChain(
    rows.workflow.original_goal,
    iterations.map((iteration) => iteration.deferredGoal),
  );
  const withGoals = iterations.map((iteration, index) =>
    Object.freeze({ ...iteration, goal: goals[index] ?? rows.workflow.original_goal }),
  );

  const workflow: WorkflowState = Object.freeze({
    id: rows.workflow.id,
    parentRunId: rows.workflow.parent_run_id,
    originalGoal: rows.workflow.original_goal,
    currentGoal: withGoals.at(-1)?.goal ?? rows.workflow.original_goal,
    status: rows.workflow.status,
    closedAt: rows.workflow.closed_at,
  });

  return Object.freeze({ workflow, iterations: Object.freeze(withGoals) });
}

function buildIteration(
  iteration: WorkflowRows["iterations"][number],
  attemptRows: AttemptRow[],
  planRows: PlanRow[],
  workItemRows: WorkItemRow[],
): IterationState {
  const attemptShapes = attemptRows.map((attempt) => ({
    row: attempt,
    plan: buildPlan(requirePlan(planRows, attempt)),
    workItems: workItemRows
      .filter((item) => item.attempt_id === attempt.id)
      .map(buildWorkItem),
  }));
  const declarations = orderedDeclarations(
    attemptShapes.map((shape) => ({
      id: shape.row.id,
      sequence: shape.row.sequence,
      plan: shape.plan,
    })),
  );
  const latest = declarations.at(-1);

  const attempts: AttemptState[] = attemptShapes.map((shape) =>
    Object.freeze({
      id: shape.row.id,
      sequence: shape.row.sequence,
      status: shape.row.status,
      failReason: shape.row.fail_reason,
      isConsistentWithIterationFocus: consistentWithIterationFocus(
        shape.row.sequence,
        declarations,
      ),
      plan: shape.plan,
      workItems: Object.freeze(shape.workItems),
    }),
  );

  return {
    id: iteration.id,
    sequence: iteration.sequence,
    origin: iteration.origin,
    maxAttempts: iteration.max_attempts,
    status: iteration.status,
    goal: "",
    focus: latest?.focus ?? null,
    deferredGoal: latest?.deferredGoal ?? null,
    attempts: Object.freeze(attempts),
  };
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
    declaredFocus: row.declared_focus,
    declaredDeferredGoal: row.declared_deferred_goal,
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
    description: row.description,
    spec: row.work_item_spec,
    needs: Object.freeze(decodeNeeds(row.needs)),
    summary: row.worker_summary,
    outcome: row.worker_outcome,
  });
}

/** The JSON column boundary: `needs` is a JSON-encoded WorkItemId array. */
export function encodeNeeds(needs: readonly string[]): string {
  return JSON.stringify(needs);
}

function decodeNeeds(raw: string): WorkItemState["needs"][number][] {
  const parsed: unknown = JSON.parse(raw);
  if (!Array.isArray(parsed)) {
    throw new Error(`work item needs column is not a JSON array: ${raw}`);
  }
  return parsed.map((id) => workItemIdFrom(String(id)));
}
