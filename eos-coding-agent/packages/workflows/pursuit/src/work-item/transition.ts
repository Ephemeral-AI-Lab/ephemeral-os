import {
  isWorkItemTerminal,
  type AttemptId,
  type LegId,
  type PlanId,
  type PursuitId,
  type WorkItemId,
} from "../../contracts/pursuit.js";
import type { PursuitTransaction } from "../../db/src/index.js";

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
  const key = workItemStorageKey(scope.legId, init.legGoalVersion, init.id);
  await trx
    .insertInto("work_items")
    .values({
      key,
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
        work_item_key: key,
        work_item_id: init.id,
        depends_on_work_item_id: dependency,
        leg_goal_version: init.legGoalVersion,
        created_at: now,
      })
      .execute();
  }
  await enqueueLaunch(trx, scope.pursuitId, "work_item", key);
}

export interface WorkItemRef {
  pursuitId: PursuitId;
  legId: LegId;
  attemptId: AttemptId;
  workItemKey: string;
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
    .where("key", "=", ref.workItemKey)
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
    .where("key", "=", ref.workItemKey)
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
  workItemKey: string,
): Promise<void> {
  const item = await trx
    .selectFrom("work_items")
    .select("status")
    .where("key", "=", workItemKey)
    .executeTakeFirst();
  if (!item || isWorkItemTerminal(item.status)) return;
  await trx
    .updateTable("work_items")
    .set({ status: "Cancelled", updated_at: new Date().toISOString() })
    .where("key", "=", workItemKey)
    .execute();
}

function workItemStorageKey(
  legId: LegId,
  legGoalVersion: number,
  workItemId: WorkItemId,
): string {
  return `${legId}:${String(legGoalVersion)}:${workItemId}`;
}
