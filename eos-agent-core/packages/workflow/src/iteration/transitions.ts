import {
  isWorkflowEntityTerminal,
  mintIterationId,
  type IterationId,
  type WorkflowId,
} from "@eos/contracts";
import type { WorkflowTransaction } from "@eos/db";

import { cancelAttempt, createAttempt } from "../attempt/transitions.js";
import { reconcileWorkflow } from "../workflow/transitions.js";
import { orderedDeclarations } from "./state.js";
import type { WorkflowTree } from "../workflow-tree.js";

export interface CreateIterationInit {
  sequence: number;
  origin: "initial" | "deferred_goal";
  maxAttempts: number;
}

/** Deferred-goal promotion re-enters the creation cascade here. */
export async function createIteration(
  trx: WorkflowTransaction,
  workflowId: WorkflowId,
  init: CreateIterationInit,
): Promise<IterationId> {
  const id = mintIterationId();
  const now = new Date().toISOString();
  await trx
    .insertInto("iterations")
    .values({
      id,
      workflow_id: workflowId,
      sequence: init.sequence,
      origin: init.origin,
      max_attempts: init.maxAttempts,
      status: "Running",
      created_at: now,
      updated_at: now,
    })
    .execute();
  await createAttempt(trx, { workflowId, iterationId: id }, 1);
  return id;
}

export interface IterationRef {
  workflowId: WorkflowId;
  iterationId: IterationId;
}

/**
 * Close from the last attempt (§2.16): a successful closing attempt closes
 * the iteration `Success` and either promotes the effective deferral into
 * the next iteration (the goal advances purely by derivation) or escalates
 * to the workflow; a failed closing attempt with the budget exhausted
 * closes the iteration `Failed` and escalates.
 */
export async function reconcileIteration(
  trx: WorkflowTransaction,
  tree: WorkflowTree,
  ref: IterationRef,
): Promise<void> {
  const iteration = await trx
    .selectFrom("iterations")
    .selectAll()
    .where("id", "=", ref.iterationId)
    .executeTakeFirst();
  if (!iteration || isWorkflowEntityTerminal(iteration.status)) return;

  const attempts = await trx
    .selectFrom("attempts")
    .select(["id", "sequence", "status"])
    .where("iteration_id", "=", ref.iterationId)
    .orderBy("sequence")
    .execute();
  const closing = attempts.at(-1);
  if (!closing) return;

  const now = new Date().toISOString();
  if (closing.status === "Success") {
    await trx
      .updateTable("iterations")
      .set({ status: "Success", updated_at: now })
      .where("id", "=", ref.iterationId)
      .execute();
    const deferredGoal = await effectiveDeferredGoal(trx, ref.iterationId, attempts);
    if (deferredGoal !== null) {
      await createIteration(trx, ref.workflowId, {
        sequence: iteration.sequence + 1,
        origin: "deferred_goal",
        maxAttempts: iteration.max_attempts,
      });
      return; // promotion descends; the workflow stays open
    }
    await reconcileWorkflow(trx, tree, ref.workflowId);
    return;
  }

  if (closing.status === "Failed" && attempts.length >= iteration.max_attempts) {
    await trx
      .updateTable("iterations")
      .set({ status: "Failed", updated_at: now })
      .where("id", "=", ref.iterationId)
      .execute();
    await reconcileWorkflow(trx, tree, ref.workflowId);
  }
}

/** The latest declaration's deferral, over fresh plan rows in attempt order. */
async function effectiveDeferredGoal(
  trx: WorkflowTransaction,
  iterationId: IterationId,
  attempts: readonly { id: string; sequence: number }[],
): Promise<string | null> {
  const plans = await trx
    .selectFrom("plans")
    .select(["attempt_id", "declared_focus", "declared_deferred_goal"])
    .where("iteration_id", "=", iterationId)
    .execute();
  const sequenceOf = new Map(attempts.map((attempt) => [attempt.id, attempt.sequence]));
  const declarations = orderedDeclarations(
    plans
      .map((plan) => ({
        id: plan.attempt_id,
        sequence: sequenceOf.get(plan.attempt_id) ?? 0,
        plan: {
          declaredFocus: plan.declared_focus,
          declaredDeferredGoal: plan.declared_deferred_goal,
        },
      }))
      .sort((a, b) => a.sequence - b.sequence),
  );
  return declarations.at(-1)?.deferredGoal ?? null;
}

/** Cancel own non-terminal row, then every attempt's cancel cascade. */
export async function cancelIteration(
  trx: WorkflowTransaction,
  iterationId: IterationId,
): Promise<void> {
  const iteration = await trx
    .selectFrom("iterations")
    .select("status")
    .where("id", "=", iterationId)
    .executeTakeFirst();
  if (!iteration) return;
  if (!isWorkflowEntityTerminal(iteration.status)) {
    await trx
      .updateTable("iterations")
      .set({ status: "Cancelled", updated_at: new Date().toISOString() })
      .where("id", "=", iterationId)
      .execute();
  }
  const attempts = await trx
    .selectFrom("attempts")
    .select("id")
    .where("iteration_id", "=", iterationId)
    .execute();
  for (const attempt of attempts) await cancelAttempt(trx, attempt.id);
}
