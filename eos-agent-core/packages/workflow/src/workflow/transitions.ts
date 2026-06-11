import {
  isWorkflowEntityTerminal,
  type AgentRunId,
  type WorkflowId,
} from "@eos/contracts";
import type { WorkflowTransaction } from "@eos/db";

import { cancelIteration, createIteration } from "../iteration/transitions.js";
import type { WorkflowTree } from "../workflow-tree.js";

export interface CreateWorkflowInit {
  workflowId: WorkflowId;
  parentRunId: AgentRunId;
  goal: string;
  maxAttempts: number;
}

/** `delegate` enters the creation cascade here (§2.22). */
export async function createWorkflow(
  trx: WorkflowTransaction,
  init: CreateWorkflowInit,
): Promise<void> {
  const now = new Date().toISOString();
  await trx
    .insertInto("workflows")
    .values({
      id: init.workflowId,
      parent_run_id: init.parentRunId,
      goal: init.goal,
      status: "Running",
      created_at: now,
      updated_at: now,
      closed_at: null,
    })
    .execute();
  await createIteration(trx, init.workflowId, {
    sequence: 1,
    origin: "initial",
    maxAttempts: init.maxAttempts,
  });
}

/**
 * Terminal close: the latest iteration's terminal status is the
 * workflow's. Promotion never reaches here - `reconcileIteration` descends
 * into `createIteration` instead of escalating when a deferral exists.
 */
export async function reconcileWorkflow(
  trx: WorkflowTransaction,
  _tree: WorkflowTree,
  workflowId: WorkflowId,
): Promise<void> {
  const workflow = await trx
    .selectFrom("workflows")
    .select("status")
    .where("id", "=", workflowId)
    .executeTakeFirst();
  if (!workflow || isWorkflowEntityTerminal(workflow.status)) return;

  const iterations = await trx
    .selectFrom("iterations")
    .select("status")
    .where("workflow_id", "=", workflowId)
    .orderBy("sequence")
    .execute();
  const last = iterations.at(-1);
  if (!last || (last.status !== "Success" && last.status !== "Failed")) return;

  const now = new Date().toISOString();
  await trx
    .updateTable("workflows")
    .set({ status: last.status, updated_at: now, closed_at: now })
    .where("id", "=", workflowId)
    .execute();
}

/**
 * Heads the top-down cancel cascade (§2.22): cancel the own non-terminal
 * row, then every iteration's cascade. Terminal subtrees no-op, which
 * keeps late settlements and cancel races harmless.
 */
export async function cancelWorkflow(
  trx: WorkflowTransaction,
  workflowId: WorkflowId,
): Promise<void> {
  const workflow = await trx
    .selectFrom("workflows")
    .select("status")
    .where("id", "=", workflowId)
    .executeTakeFirst();
  if (!workflow) return;
  if (!isWorkflowEntityTerminal(workflow.status)) {
    const now = new Date().toISOString();
    await trx
      .updateTable("workflows")
      .set({ status: "Cancelled", updated_at: now, closed_at: now })
      .where("id", "=", workflowId)
      .execute();
  }
  const iterations = await trx
    .selectFrom("iterations")
    .select("id")
    .where("workflow_id", "=", workflowId)
    .execute();
  for (const iteration of iterations) await cancelIteration(trx, iteration.id);
}
