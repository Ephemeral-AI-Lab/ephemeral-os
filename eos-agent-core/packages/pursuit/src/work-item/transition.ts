import {
  isWorkItemTerminal,
  type AttemptId,
  type LegId,
  type PlanId,
  type PursuitId,
  type WorkItemId,
} from "@eos/contracts";
import type { PursuitTransaction } from "@eos/db";

import {
  propagateDependencyBlocks,
  reconcileAttemptStatus,
} from "../attempt/transition.js";
import { enqueueLaunch } from "../agent-launcher.js";
import { encodeDependsOn, type PursuitTree } from "../pursuit-tree.js";

export interface WorkItemScope {
  pursuitId: PursuitId;
  legId: LegId;
  attemptId: AttemptId;
  planId: PlanId;
}

export interface WorkItemInit {
  id: WorkItemId;
  agentName: string;
  title: string;
  spec: string;
  dependsOn: readonly WorkItemId[];
  legGoalVersion: number;
}

export async function createWorkItem(
  trx: PursuitTransaction,
  scope: WorkItemScope,
  init: WorkItemInit,
): Promise<void> {
  const now = new Date().toISOString();
  await trx
    .insertInto("work_items")
    .values({
      id: init.id,
      pursuit_id: scope.pursuitId,
      leg_id: scope.legId,
      attempt_id: scope.attemptId,
      plan_id: scope.planId,
      agent_name: init.agentName,
      agent_run_id: null,
      status: "NotStarted",
      title: init.title,
      spec: init.spec,
      depends_on: encodeDependsOn(init.dependsOn),
      leg_goal_version: init.legGoalVersion,
      worker_summary: null,
      worker_outcome: null,
      created_at: now,
      updated_at: now,
    })
    .execute();
  for (const dependency of init.dependsOn) {
    await trx
      .insertInto("work_item_dependency_edges")
      .values({
        pursuit_id: scope.pursuitId,
        leg_id: scope.legId,
        attempt_id: scope.attemptId,
        work_item_id: init.id,
        depends_on_work_item_id: dependency,
        leg_goal_version: init.legGoalVersion,
        created_at: now,
      })
      .execute();
  }
  await enqueueLaunch(trx, scope.pursuitId, "work_item", init.id);
}

export interface WorkItemRef {
  pursuitId: PursuitId;
  legId: LegId;
  attemptId: AttemptId;
  workItemId: WorkItemId;
}

export interface WorkerOutcomeRecord {
  isPass: boolean;
  summary: string;
  outcome: string;
}

export async function applyWorkItemSettlement(
  trx: PursuitTransaction,
  tree: PursuitTree,
  ref: WorkItemRef,
  record: WorkerOutcomeRecord,
): Promise<void> {
  const item = await trx
    .selectFrom("work_items")
    .select(["status"])
    .where("id", "=", ref.workItemId)
    .executeTakeFirst();
  if (!item || isWorkItemTerminal(item.status)) return;

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

  await propagateDependencyBlocks(trx, ref.attemptId);
  await reconcileAttemptStatus(trx, tree, {
    pursuitId: ref.pursuitId,
    legId: ref.legId,
    attemptId: ref.attemptId,
  });
}

export async function cancelWorkItem(
  trx: PursuitTransaction,
  workItemId: WorkItemId,
): Promise<void> {
  const item = await trx
    .selectFrom("work_items")
    .select("status")
    .where("id", "=", workItemId)
    .executeTakeFirst();
  if (!item || isWorkItemTerminal(item.status)) return;
  await trx
    .updateTable("work_items")
    .set({ status: "Cancelled", updated_at: new Date().toISOString() })
    .where("id", "=", workItemId)
    .execute();
}
