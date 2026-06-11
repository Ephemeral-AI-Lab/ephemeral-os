import {
  isWorkflowEntityTerminal,
  mintAttemptId,
  type AttemptId,
  type IterationId,
  type WorkflowId,
} from "@eos/contracts";
import type { WorkflowTransaction } from "@eos/db";

import { reconcileIteration } from "../iteration/transitions.js";
import { cancelPlan, createPlan } from "../plan/transitions.js";
import { cancelWorkItem } from "../work-item/transitions.js";
import type { WorkflowTree } from "../workflow-tree.js";

export interface AttemptScope {
  workflowId: WorkflowId;
  iterationId: IterationId;
}

/** Retry re-enters the creation cascade here: attempt + plan + enqueue. */
export async function createAttempt(
  trx: WorkflowTransaction,
  scope: AttemptScope,
  sequence: number,
): Promise<AttemptId> {
  const id = mintAttemptId();
  const now = new Date().toISOString();
  await trx
    .insertInto("attempts")
    .values({
      id,
      workflow_id: scope.workflowId,
      iteration_id: scope.iterationId,
      sequence,
      status: "NotStarted",
      fail_reason: null,
      created_at: now,
      updated_at: now,
    })
    .execute();
  await createPlan(trx, { ...scope, attemptId: id });
  return id;
}

export interface AttemptRef {
  workflowId: WorkflowId;
  iterationId: IterationId;
  attemptId: AttemptId;
}

export interface AttemptReconcileContext {
  /** The precise failure that drove this reconcile, recorded on the row. */
  failReason?: string;
}

/**
 * Re-derive the attempt's status from its fresh child rows and act: all
 * items `Success` closes it upward; a failed plan or item fails it, with a
 * retry attempt while the iteration budget (which counts every attempt,
 * refocused or not - §2.4) has room, escalation otherwise.
 */
export async function reconcileAttempt(
  trx: WorkflowTransaction,
  tree: WorkflowTree,
  ref: AttemptRef,
  context: AttemptReconcileContext = {},
): Promise<void> {
  const attempt = await trx
    .selectFrom("attempts")
    .selectAll()
    .where("id", "=", ref.attemptId)
    .executeTakeFirst();
  if (!attempt || isWorkflowEntityTerminal(attempt.status)) return;

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

  const failedItem = items.find((item) => item.status === "Failed");
  let next: "Success" | "Failed";
  let failReason: string | null = null;
  if (plan?.status === "Failed") {
    next = "Failed";
    failReason = context.failReason ?? "planner failed without a submission";
  } else if (failedItem) {
    next = "Failed";
    failReason =
      context.failReason ??
      `work_item ${failedItem.id} failed: ${failedItem.worker_summary ?? "no summary"}`;
  } else if (
    plan?.status === "Success" &&
    items.length > 0 &&
    items.every((item) => item.status === "Success")
  ) {
    next = "Success";
  } else {
    return; // still in progress; nothing to derive
  }

  const now = new Date().toISOString();
  await trx
    .updateTable("attempts")
    .set({ status: next, fail_reason: failReason, updated_at: now })
    .where("id", "=", ref.attemptId)
    .execute();

  if (next === "Failed") {
    const iteration = await trx
      .selectFrom("iterations")
      .select(["status", "max_attempts"])
      .where("id", "=", ref.iterationId)
      .executeTakeFirst();
    const attemptCount = await trx
      .selectFrom("attempts")
      .select(trx.fn.countAll<number>().as("count"))
      .where("iteration_id", "=", ref.iterationId)
      .executeTakeFirst();
    const spent = attemptCount?.count ?? 0;
    if (
      iteration &&
      !isWorkflowEntityTerminal(iteration.status) &&
      spent < iteration.max_attempts
    ) {
      await createAttempt(
        trx,
        { workflowId: ref.workflowId, iterationId: ref.iterationId },
        spent + 1,
      );
      return; // retry created; the iteration stays open
    }
  }

  await reconcileIteration(trx, tree, {
    workflowId: ref.workflowId,
    iterationId: ref.iterationId,
  });
}

/** Cancel own non-terminal row, then the children's cancel leaves. */
export async function cancelAttempt(
  trx: WorkflowTransaction,
  attemptId: AttemptId,
): Promise<void> {
  const attempt = await trx
    .selectFrom("attempts")
    .select("status")
    .where("id", "=", attemptId)
    .executeTakeFirst();
  if (!attempt) return;
  if (!isWorkflowEntityTerminal(attempt.status)) {
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
