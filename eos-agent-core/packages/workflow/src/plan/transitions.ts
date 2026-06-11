import {
  isWorkflowEntityTerminal,
  mintPlanId,
  mintWorkItemId,
  type AttemptId,
  type IterationId,
  type PlanId,
  type PlannerOutcomePayload,
  type WorkItemId,
  type WorkflowId,
} from "@eos/contracts";
import type { WorkflowTransaction } from "@eos/db";

import { reconcileAttempt } from "../attempt/transitions.js";
import { enqueueLaunch } from "../launcher.js";
import { createWorkItem } from "../work-item/transitions.js";
import type { WorkflowTree } from "../workflow-tree.js";

export interface PlanScope {
  workflowId: WorkflowId;
  iterationId: IterationId;
  attemptId: AttemptId;
}

export async function createPlan(
  trx: WorkflowTransaction,
  scope: PlanScope,
): Promise<PlanId> {
  const id = mintPlanId();
  const now = new Date().toISOString();
  await trx
    .insertInto("plans")
    .values({
      id,
      workflow_id: scope.workflowId,
      iteration_id: scope.iterationId,
      attempt_id: scope.attemptId,
      agent_run_id: null,
      status: "NotStarted",
      declared_focus: null,
      declared_deferred_goal: null,
      planner_summary: null,
      created_at: now,
      updated_at: now,
    })
    .execute();
  await enqueueLaunch(trx, scope.workflowId, "plan", id);
  return id;
}

export type PlanReconcileOutcome =
  | { kind: "submitted"; payload: PlannerOutcomePayload }
  | { kind: "failed"; reason: string };

/**
 * The planner leaf of the §2.22 cascade. An accepted submission records
 * the summary and - when `iteration_focus` is present - the declared pair
 * (superseding any prior declaration purely by being a later row), then
 * mints the described work items; nothing above the plan changes status.
 * A failure (death or compose synthesis) escalates to the attempt.
 */
export async function reconcilePlan(
  trx: WorkflowTransaction,
  tree: WorkflowTree,
  planId: PlanId,
  outcome: PlanReconcileOutcome,
): Promise<void> {
  const plan = await trx
    .selectFrom("plans")
    .selectAll()
    .where("id", "=", planId)
    .executeTakeFirst();
  if (!plan || isWorkflowEntityTerminal(plan.status)) return;

  const scope = {
    workflowId: plan.workflow_id,
    iterationId: plan.iteration_id,
    attemptId: plan.attempt_id,
  };
  const now = new Date().toISOString();

  if (outcome.kind === "failed") {
    await trx
      .updateTable("plans")
      .set({ status: "Failed", updated_at: now })
      .where("id", "=", planId)
      .execute();
    await reconcileAttempt(trx, tree, scope, { failReason: outcome.reason });
    return;
  }

  const payload = outcome.payload;
  const declares = payload.iteration_focus !== undefined;
  await trx
    .updateTable("plans")
    .set({
      status: "Success",
      planner_summary: payload.summary,
      ...(declares && {
        declared_focus: payload.iteration_focus,
        declared_deferred_goal: payload.deferred_goal ?? null,
      }),
      updated_at: now,
    })
    .where("id", "=", planId)
    .execute();

  // Mint global ids and rewrite planner-local `needs` references.
  const minted = new Map<string, WorkItemId>(
    payload.work_items.map((item) => [item.id, mintWorkItemId()]),
  );
  const mintedId = (local: string): WorkItemId => {
    const id = minted.get(local);
    if (!id) throw new Error(`work item needs reference "${local}" was not declared`);
    return id;
  };
  for (const item of payload.work_items) {
    await createWorkItem(
      trx,
      { ...scope, planId },
      {
        id: mintedId(item.id),
        agentName: item.agent_name,
        description: item.description,
        spec: item.work_item_spec,
        needs: item.needs.map(mintedId),
      },
    );
  }
}

/** Cancel cascade leaf; terminal rows no-op. */
export async function cancelPlan(
  trx: WorkflowTransaction,
  planId: PlanId,
): Promise<void> {
  const plan = await trx
    .selectFrom("plans")
    .select("status")
    .where("id", "=", planId)
    .executeTakeFirst();
  if (!plan || isWorkflowEntityTerminal(plan.status)) return;
  await trx
    .updateTable("plans")
    .set({ status: "Cancelled", updated_at: new Date().toISOString() })
    .where("id", "=", planId)
    .execute();
}
