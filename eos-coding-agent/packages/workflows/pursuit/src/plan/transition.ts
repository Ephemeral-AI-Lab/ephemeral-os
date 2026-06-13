import {
  isPursuitEntityTerminal,
  mintPlanId,
  workItemIdFrom,
  type AttemptId,
  type LegId,
  type PlanId,
  type PlannerOutcomePayload,
  type PursuitId,
} from "../../contracts/pursuit.js";
import type { PursuitTransaction } from "../../db/src/index.js";

import {
  plannerFailureReason,
  propagateDependencyBlocks,
  reconcileAttemptStatus,
} from "../attempt/transition.js";
import { enqueueLaunch } from "../agent-launcher.js";
import { createWorkItem } from "../work-item/transition.js";
import type { PursuitTree } from "../pursuit-tree.js";

export interface PlanScope {
  pursuitId: PursuitId;
  legId: LegId;
  attemptId: AttemptId;
}

export async function createPlan(
  trx: PursuitTransaction,
  scope: PlanScope,
  legGoalVersion: number,
): Promise<PlanId> {
  const id = mintPlanId();
  const now = new Date().toISOString();
  await trx
    .insertInto("plans")
    .values({
      id,
      pursuit_id: scope.pursuitId,
      leg_id: scope.legId,
      attempt_id: scope.attemptId,
      agent_run_id: null,
      status: "NotStarted",
      declared_leg_goal: null,
      declared_next_leg_goal: null,
      leg_goal_version: legGoalVersion,
      planner_summary: null,
      created_at: now,
      updated_at: now,
    })
    .execute();
  await enqueueLaunch(trx, scope.pursuitId, "plan", id);
  return id;
}

export type PlannerSettlement =
  | { kind: "submitted"; payload: PlannerOutcomePayload }
  | { kind: "failed"; reason: string };

export async function applyPlannerSettlement(
  trx: PursuitTransaction,
  tree: PursuitTree,
  planId: PlanId,
  settlement: PlannerSettlement,
): Promise<void> {
  const plan = await trx
    .selectFrom("plans")
    .selectAll()
    .where("id", "=", planId)
    .executeTakeFirst();
  if (!plan || isPursuitEntityTerminal(plan.status)) return;

  const scope = {
    pursuitId: plan.pursuit_id,
    legId: plan.leg_id,
    attemptId: plan.attempt_id,
  };
  const now = new Date().toISOString();

  if (settlement.kind === "failed") {
    await trx
      .updateTable("plans")
      .set({ status: "Failed", updated_at: now })
      .where("id", "=", planId)
      .execute();
    await reconcileAttemptStatus(trx, tree, scope, {
      failureReasons: [plannerFailureReason(settlement.reason)],
    });
    return;
  }

  const payload = settlement.payload;
  const leg = await trx
    .selectFrom("legs")
    .selectAll()
    .where("id", "=", plan.leg_id)
    .executeTakeFirstOrThrow();
  let legGoalVersion = leg.leg_goal_version;
  if (payload.leg_goal !== undefined) {
    legGoalVersion += 1;
    await trx
      .updateTable("legs")
      .set({
        leg_goal: payload.leg_goal,
        leg_goal_version: legGoalVersion,
        leg_goal_provenance: `declared by attempt_${plan.attempt_id} planner`,
        next_leg_goal: payload.next_leg_goal ?? null,
        updated_at: now,
      })
      .where("id", "=", leg.id)
      .execute();
    await trx
      .updateTable("attempts")
      .set({ leg_goal_version: legGoalVersion, updated_at: now })
      .where("id", "=", plan.attempt_id)
      .execute();
  } else if (payload.next_leg_goal !== undefined) {
    await trx
      .updateTable("legs")
      .set({ next_leg_goal: payload.next_leg_goal, updated_at: now })
      .where("id", "=", leg.id)
      .execute();
  }

  await trx
    .updateTable("plans")
    .set({
      status: "Success",
      declared_leg_goal: payload.leg_goal ?? null,
      declared_next_leg_goal: payload.next_leg_goal ?? null,
      leg_goal_version: legGoalVersion,
      planner_summary: payload.summary,
      updated_at: now,
    })
    .where("id", "=", planId)
    .execute();

  for (const item of payload.work_items) {
    await createWorkItem(
      trx,
      { ...scope, planId },
      {
        id: workItemIdFrom(item.id),
        agentName: item.agent_name,
        title: item.title,
        spec: item.spec,
        dependsOn: item.depends_on.map(workItemIdFrom),
        legGoalVersion,
      },
    );
  }
  await propagateDependencyBlocks(trx, plan.attempt_id);
  await reconcileAttemptStatus(trx, tree, scope);
}

export async function cancelPlan(
  trx: PursuitTransaction,
  planId: PlanId,
): Promise<void> {
  const plan = await trx
    .selectFrom("plans")
    .select("status")
    .where("id", "=", planId)
    .executeTakeFirst();
  if (!plan || isPursuitEntityTerminal(plan.status)) return;
  await trx
    .updateTable("plans")
    .set({ status: "Cancelled", updated_at: new Date().toISOString() })
    .where("id", "=", planId)
    .execute();
}
