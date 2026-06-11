import type {
  AttemptId,
  IterationId,
  PlanId,
  PlannerContextInput,
  WorkItemId,
  WorkerContextInput,
  WorkflowContextAttempt,
  WorkflowContextIteration,
  WorkflowContextSnapshot,
} from "@eos/contracts";

import { attemptDirPath, iterationDirName, workflowRootPath } from "../archive/paths.js";
import type { AttemptState } from "../attempt/state.js";
import type { IterationState } from "../iteration/state.js";
import type { WorkflowTree } from "../workflow-tree.js";

export interface PlannerLaunchLocator {
  iterationId: IterationId;
  attemptId: AttemptId;
  planId: PlanId;
}

export interface WorkerLaunchLocator {
  iterationId: IterationId;
  attemptId: AttemptId;
  workItemId: WorkItemId;
}

/**
 * Pure §7 script-input builders over the `WorkflowTree`: the full
 * serialized `workflow_context` snapshot plus the current launch locator.
 * No convenience variables are precomputed - the composer (default policy
 * or user script) derives its own locals from `workflow_context`.
 */
export function buildPlannerContextInput(
  tree: WorkflowTree,
  locator: PlannerLaunchLocator,
): PlannerContextInput {
  return {
    kind: "planner",
    workflow_context: snapshotWorkflowContext(tree),
    current: {
      workflow_id: tree.workflow.id,
      iteration_id: locator.iterationId,
      attempt_id: locator.attemptId,
      plan_id: locator.planId,
    },
  };
}

export function buildWorkerContextInput(
  tree: WorkflowTree,
  locator: WorkerLaunchLocator,
): WorkerContextInput {
  return {
    kind: "worker",
    workflow_context: snapshotWorkflowContext(tree),
    current: {
      workflow_id: tree.workflow.id,
      iteration_id: locator.iterationId,
      attempt_id: locator.attemptId,
      work_item_id: locator.workItemId,
    },
  };
}

/** ALL facts, including ones the default policy hides - hiding is policy. */
export function snapshotWorkflowContext(tree: WorkflowTree): WorkflowContextSnapshot {
  const root = workflowRootPath(tree.workflow.id);
  return {
    workflow: {
      id: tree.workflow.id,
      original_goal: tree.workflow.originalGoal,
      current_goal: tree.workflow.currentGoal,
      status: tree.workflow.status,
      context_path: root,
      iterations: tree.iterations.map((iteration) =>
        snapshotIteration(root, iteration),
      ),
    },
  };
}

function snapshotIteration(
  root: string,
  iteration: IterationState,
): WorkflowContextIteration {
  return {
    id: iteration.id,
    sequence: iteration.sequence,
    origin: iteration.origin,
    status: iteration.status,
    focus: iteration.focus,
    deferred_goal: iteration.deferredGoal,
    max_attempts: iteration.maxAttempts,
    context_path: `${root}/${iterationDirName(iteration.id)}`,
    attempts: iteration.attempts.map((attempt) =>
      snapshotAttempt(root, iteration, attempt),
    ),
  };
}

function snapshotAttempt(
  root: string,
  iteration: IterationState,
  attempt: AttemptState,
): WorkflowContextAttempt {
  const attemptPath = `${root}/${attemptDirPath(iteration, attempt)}`;
  return {
    id: attempt.id,
    sequence: attempt.sequence,
    status: attempt.status,
    fail_reason: attempt.failReason,
    is_consistent_with_iteration_focus: attempt.isConsistentWithIterationFocus,
    context_path: attemptPath,
    plan: {
      id: attempt.plan.id,
      status: attempt.plan.status,
      declared_focus: attempt.plan.declaredFocus,
      declared_deferred_goal: attempt.plan.declaredDeferredGoal,
      summary: attempt.plan.summary,
      agent_run_id: attempt.plan.agentRunId,
      context_path: `${attemptPath}/plan_${attempt.plan.id}`,
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
