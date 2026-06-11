import {
  isWorkflowEntityTerminal,
  type AttemptId,
  type IterationId,
  type PlanId,
  type WorkItemId,
  type WorkflowId,
} from "@eos/contracts";
import type { WorkflowTransaction } from "@eos/db";

import { reconcileAttempt } from "../attempt/transitions.js";
import { enqueueLaunch } from "../launcher.js";
import { encodeNeeds, type WorkflowTree } from "../workflow-tree.js";

export interface WorkItemScope {
  workflowId: WorkflowId;
  iterationId: IterationId;
  attemptId: AttemptId;
  planId: PlanId;
}

export interface WorkItemInit {
  id: WorkItemId;
  agentName: string;
  description: string;
  spec: string;
  needs: readonly WorkItemId[];
}

/** Minted by `reconcilePlan`; every item enqueues and waits for readiness. */
export async function createWorkItem(
  trx: WorkflowTransaction,
  scope: WorkItemScope,
  init: WorkItemInit,
): Promise<void> {
  const now = new Date().toISOString();
  await trx
    .insertInto("work_items")
    .values({
      id: init.id,
      workflow_id: scope.workflowId,
      iteration_id: scope.iterationId,
      attempt_id: scope.attemptId,
      plan_id: scope.planId,
      agent_name: init.agentName,
      agent_run_id: null,
      status: "NotStarted",
      description: init.description,
      work_item_spec: init.spec,
      needs: encodeNeeds(init.needs),
      worker_summary: null,
      worker_outcome: null,
      created_at: now,
      updated_at: now,
    })
    .execute();
  await enqueueLaunch(trx, scope.workflowId, "work_item", init.id);
}

export interface WorkItemRef {
  workflowId: WorkflowId;
  iterationId: IterationId;
  attemptId: AttemptId;
  workItemId: WorkItemId;
}

export interface WorkerOutcomeRecord {
  isPass: boolean;
  summary: string;
  outcome: string;
}

/**
 * The worker leaf of the §2.22 reconcile cascade: live submissions and
 * death/compose synthesis both land here. A failing item cancels its
 * non-terminal siblings in the same transaction (§2.20) before escalating.
 */
export async function reconcileWorkItem(
  trx: WorkflowTransaction,
  tree: WorkflowTree,
  ref: WorkItemRef,
  record: WorkerOutcomeRecord,
): Promise<void> {
  const item = await trx
    .selectFrom("work_items")
    .selectAll()
    .where("id", "=", ref.workItemId)
    .executeTakeFirst();
  if (!item || isWorkflowEntityTerminal(item.status)) return;

  const now = new Date().toISOString();
  await trx
    .updateTable("work_items")
    .set({
      status: record.isPass ? "Success" : "Failed",
      worker_summary: record.summary,
      worker_outcome: record.outcome,
      updated_at: now,
    })
    .where("id", "=", ref.workItemId)
    .execute();

  if (!record.isPass) {
    const siblings = await trx
      .selectFrom("work_items")
      .select(["id", "status"])
      .where("attempt_id", "=", ref.attemptId)
      .where("id", "!=", ref.workItemId)
      .execute();
    for (const sibling of siblings) {
      if (!isWorkflowEntityTerminal(sibling.status)) {
        await cancelWorkItem(trx, sibling.id);
      }
    }
  }

  await reconcileAttempt(
    trx,
    tree,
    { workflowId: ref.workflowId, iterationId: ref.iterationId, attemptId: ref.attemptId },
    record.isPass
      ? {}
      : { failReason: `work_item ${ref.workItemId} failed: ${record.summary}` },
  );
}

/** Cancel cascade leaf and the §2.20 sibling cancel; terminal rows no-op. */
export async function cancelWorkItem(
  trx: WorkflowTransaction,
  workItemId: WorkItemId,
): Promise<void> {
  const item = await trx
    .selectFrom("work_items")
    .select("status")
    .where("id", "=", workItemId)
    .executeTakeFirst();
  if (!item || isWorkflowEntityTerminal(item.status)) return;
  await trx
    .updateTable("work_items")
    .set({ status: "Cancelled", updated_at: new Date().toISOString() })
    .where("id", "=", workItemId)
    .execute();
}
