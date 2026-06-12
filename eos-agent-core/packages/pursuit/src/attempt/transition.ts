import {
  isPursuitEntityTerminal,
  mintAttemptId,
  type AttemptId,
  type LegId,
  type PursuitId,
  type WorkItemRunStatus,
} from "@eos/contracts";
import type { PursuitTransaction } from "@eos/db";

import { reconcileLeg } from "../leg/transition.js";
import { cancelPlan, createPlan } from "../plan/transition.js";
import { encodeStringList, type PursuitTree } from "../pursuit-tree.js";
import { cancelWorkItem } from "../work-item/transition.js";

export interface AttemptScope {
  pursuitId: PursuitId;
  legId: LegId;
}

export async function createAttempt(
  trx: PursuitTransaction,
  scope: AttemptScope,
  sequence: number,
): Promise<AttemptId> {
  const leg = await trx
    .selectFrom("legs")
    .select("leg_goal_version")
    .where("id", "=", scope.legId)
    .executeTakeFirstOrThrow();
  const id = mintAttemptId();
  const now = new Date().toISOString();
  await trx
    .insertInto("attempts")
    .values({
      id,
      pursuit_id: scope.pursuitId,
      leg_id: scope.legId,
      sequence,
      leg_goal_version: leg.leg_goal_version,
      status: "NotStarted",
      failure_reasons: encodeStringList([]),
      created_at: now,
      updated_at: now,
    })
    .execute();
  await createPlan(trx, { ...scope, attemptId: id }, leg.leg_goal_version);
  return id;
}

export interface AttemptRef {
  pursuitId: PursuitId;
  legId: LegId;
  attemptId: AttemptId;
}

export interface AttemptSettlementContext {
  failureReasons?: readonly string[];
}

export async function propagateDependencyBlocks(
  trx: PursuitTransaction,
  attemptId: AttemptId,
): Promise<void> {
  const attempt = await trx
    .selectFrom("attempts")
    .select(["leg_id", "leg_goal_version"])
    .where("id", "=", attemptId)
    .executeTakeFirst();
  if (!attempt) return;

  let changed = true;
  while (changed) {
    changed = false;
    const allVersionItems = await trx
      .selectFrom("work_items")
      .select(["id", "attempt_id", "status", "depends_on"])
      .where("leg_id", "=", attempt.leg_id)
      .where("leg_goal_version", "=", attempt.leg_goal_version)
      .execute();
    const statusOf = new Map(
      allVersionItems.map((item) => [String(item.id), item.status]),
    );
    for (const item of allVersionItems) {
      if (item.attempt_id !== attemptId || item.status !== "NotStarted") continue;
      const dependsOn = decodeDependsOn(item.depends_on);
      if (!dependsOn.some((id) => dependencyBlocks(statusOf.get(id)))) continue;
      await trx
        .updateTable("work_items")
        .set({
          status: "Blocked",
          worker_summary: "blocked by failed dependency",
          updated_at: new Date().toISOString(),
        })
        .where("id", "=", item.id)
        .execute();
      changed = true;
    }
  }
}

export async function reconcileAttemptStatus(
  trx: PursuitTransaction,
  tree: PursuitTree,
  ref: AttemptRef,
  context: AttemptSettlementContext = {},
): Promise<void> {
  const attempt = await trx
    .selectFrom("attempts")
    .selectAll()
    .where("id", "=", ref.attemptId)
    .executeTakeFirst();
  if (!attempt || isPursuitEntityTerminal(attempt.status)) return;

  const plan = await trx
    .selectFrom("plans")
    .select(["id", "status"])
    .where("attempt_id", "=", ref.attemptId)
    .executeTakeFirst();
  const items = await trx
    .selectFrom("work_items")
    .select(["id", "status", "worker_summary"])
    .where("attempt_id", "=", ref.attemptId)
    .execute();

  let next: "Success" | "Failed" | undefined;
  let failureReasons: readonly string[] = [];
  if (plan?.status === "Failed") {
    next = "Failed";
    failureReasons = context.failureReasons ?? ["planner failed without a submission"];
  } else if (
    plan?.status === "Success" &&
    items.length > 0 &&
    items.every((item) => item.status === "Success")
  ) {
    next = "Success";
  } else if (
    plan?.status === "Success" &&
    items.some((item) => item.status === "Failed" || item.status === "Blocked") &&
    items.every((item) => item.status !== "Running" && item.status !== "NotStarted")
  ) {
    next = "Failed";
    failureReasons = context.failureReasons ?? itemFailureReasons(items);
  }

  if (next === undefined) return;

  const now = new Date().toISOString();
  await trx
    .updateTable("attempts")
    .set({
      status: next,
      failure_reasons: encodeStringList(failureReasons),
      updated_at: now,
    })
    .where("id", "=", ref.attemptId)
    .execute();

  if (next === "Failed") {
    const leg = await trx
      .selectFrom("legs")
      .select(["status", "max_attempts"])
      .where("id", "=", ref.legId)
      .executeTakeFirst();
    const attemptCount = await trx
      .selectFrom("attempts")
      .select(trx.fn.countAll<number>().as("count"))
      .where("leg_id", "=", ref.legId)
      .executeTakeFirst();
    const spent = attemptCount?.count ?? 0;
    if (
      leg &&
      !isPursuitEntityTerminal(leg.status) &&
      spent < leg.max_attempts
    ) {
      await createAttempt(trx, { pursuitId: ref.pursuitId, legId: ref.legId }, spent + 1);
      return;
    }
  }

  await reconcileLeg(trx, tree, {
    pursuitId: ref.pursuitId,
    legId: ref.legId,
  });
}

export async function cancelAttempt(
  trx: PursuitTransaction,
  attemptId: AttemptId,
): Promise<void> {
  const attempt = await trx
    .selectFrom("attempts")
    .select("status")
    .where("id", "=", attemptId)
    .executeTakeFirst();
  if (!attempt) return;
  if (!isPursuitEntityTerminal(attempt.status)) {
    await trx
      .updateTable("attempts")
      .set({ status: "Cancelled", updated_at: new Date().toISOString() })
      .where("id", "=", attemptId)
      .execute();
  }
  const plans = await trx
    .selectFrom("plans")
    .select("id")
    .where("attempt_id", "=", attemptId)
    .execute();
  for (const plan of plans) await cancelPlan(trx, plan.id);
  const items = await trx
    .selectFrom("work_items")
    .select("id")
    .where("attempt_id", "=", attemptId)
    .execute();
  for (const item of items) await cancelWorkItem(trx, item.id);
}

function dependencyBlocks(status: WorkItemRunStatus | undefined): boolean {
  return status === "Failed" || status === "Blocked";
}

function itemFailureReasons(
  items: readonly { id: string; status: WorkItemRunStatus; worker_summary: string | null }[],
): string[] {
  return items
    .filter((item) => item.status === "Failed" || item.status === "Blocked")
    .map((item) =>
      item.status === "Blocked"
        ? `work_item ${item.id} blocked by failed dependency`
        : `work_item ${item.id} failed: ${item.worker_summary ?? "no summary"}`,
    );
}

function decodeDependsOn(raw: string): string[] {
  const parsed: unknown = JSON.parse(raw);
  return Array.isArray(parsed) ? parsed.map(String) : [];
}
