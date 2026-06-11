import { randomUUID } from "node:crypto";

import {
  isWorkflowEntityTerminal,
  planIdFrom,
  workItemIdFrom,
  type AgentRunId,
  type AttemptId,
  type InitialUserMessage,
  type IterationId,
  type JsonValue,
  type PlanId,
  type SubmissionBinding,
  type WorkItemId,
  type WorkflowId,
} from "@eos/contracts";
import type { WorkflowDb, WorkflowTransaction } from "@eos/db";

import { workItemReady } from "./work-item/state.js";

// --- the launch port (Phase 05 §2.16 contract + the §2.19/§2.21 seams) ---------

export interface LaunchSettlement {
  status: "completed" | "failed" | "cancelled";
  /** `outcome.submission` verbatim; unused by the bound-submission model. */
  submission?: JsonValue;
}

export interface LaunchedAgent {
  runId: AgentRunId;
  outcome: Promise<LaunchSettlement>;
  interrupt(reason: string): void;
}

export interface AgentLaunchOptions {
  /** The §2.19 entity-bound submission seam for the child's terminal tool. */
  submission?: SubmissionBinding;
  /** The shared workflow cancellation signal (§2.21). */
  signal?: AbortSignal;
  /** The delegating run, stamped as the child's parent. */
  parent?: AgentRunId;
}

export interface AgentLaunchPort {
  launch(
    agentName: string,
    initialMessages: readonly InitialUserMessage[],
    options?: AgentLaunchOptions,
  ): LaunchedAgent;
}

// --- launch queue ----------------------------------------------------------------

export async function enqueueLaunch(
  trx: WorkflowTransaction,
  workflowId: WorkflowId,
  kind: "plan" | "work_item",
  entityId: string,
): Promise<void> {
  await trx
    .insertInto("launch_queue")
    .values({
      workflow_id: workflowId,
      kind,
      entity_id: entityId,
      state: "queued",
      launch_token: null,
      created_at: new Date().toISOString(),
    })
    .execute();
}

export type ClaimedLaunch =
  | {
      kind: "plan";
      workflowId: WorkflowId;
      iterationId: IterationId;
      attemptId: AttemptId;
      planId: PlanId;
      agentName: string;
      launchToken: string;
      queueId: number;
    }
  | {
      kind: "work_item";
      workflowId: WorkflowId;
      iterationId: IterationId;
      attemptId: AttemptId;
      workItemId: WorkItemId;
      agentName: string;
      launchToken: string;
      queueId: number;
    };

/**
 * Claim every launchable queued row inside the mutation transaction: the
 * entity flips to `Running` and the row to `claimed` with a fresh
 * `launch_token`. Launchable = a `NotStarted` plan on a non-terminal
 * attempt, or a `NotStarted` work item on a `Running` attempt whose
 * `needs` are all `Success`. Nothing launches here - the post-commit
 * launcher does, after rechecking the token.
 */
export async function claimLaunchable(
  trx: WorkflowTransaction,
  workflowId: WorkflowId,
  plannerAgentName: string,
): Promise<ClaimedLaunch[]> {
  const workflow = await trx
    .selectFrom("workflows")
    .select("status")
    .where("id", "=", workflowId)
    .executeTakeFirst();
  if (workflow?.status !== "Running") return [];

  const queued = await trx
    .selectFrom("launch_queue")
    .selectAll()
    .where("workflow_id", "=", workflowId)
    .where("state", "=", "queued")
    .orderBy("id")
    .execute();

  const now = new Date().toISOString();
  const claims: ClaimedLaunch[] = [];
  for (const row of queued) {
    if (row.kind === "plan") {
      const plan = await trx
        .selectFrom("plans")
        .selectAll()
        .where("id", "=", planIdFrom(row.entity_id))
        .executeTakeFirst();
      if (plan?.status !== "NotStarted") continue;
      const attempt = await trx
        .selectFrom("attempts")
        .select("status")
        .where("id", "=", plan.attempt_id)
        .executeTakeFirst();
      if (!attempt || isWorkflowEntityTerminal(attempt.status)) continue;
      const launchToken = randomUUID();
      await trx
        .updateTable("plans")
        .set({ status: "Running", updated_at: now })
        .where("id", "=", plan.id)
        .execute();
      if (attempt.status === "NotStarted") {
        await trx
          .updateTable("attempts")
          .set({ status: "Running", updated_at: now })
          .where("id", "=", plan.attempt_id)
          .execute();
      }
      await trx
        .updateTable("launch_queue")
        .set({ state: "claimed", launch_token: launchToken })
        .where("id", "=", row.id)
        .execute();
      claims.push({
        kind: "plan",
        workflowId,
        iterationId: plan.iteration_id,
        attemptId: plan.attempt_id,
        planId: plan.id,
        agentName: plannerAgentName,
        launchToken,
        queueId: row.id,
      });
      continue;
    }

    const item = await trx
      .selectFrom("work_items")
      .selectAll()
      .where("id", "=", workItemIdFrom(row.entity_id))
      .executeTakeFirst();
    if (item?.status !== "NotStarted") continue;
    const attempt = await trx
      .selectFrom("attempts")
      .select("status")
      .where("id", "=", item.attempt_id)
      .executeTakeFirst();
    if (attempt?.status !== "Running") continue;
    const siblings = await trx
      .selectFrom("work_items")
      .select(["id", "status"])
      .where("attempt_id", "=", item.attempt_id)
      .execute();
    const statusOf = new Map(siblings.map((sibling) => [String(sibling.id), sibling.status]));
    const needs: unknown = JSON.parse(item.needs);
    if (!Array.isArray(needs)) continue;
    if (!workItemReady(needs.map(String), (id) => statusOf.get(id))) continue;

    const launchToken = randomUUID();
    await trx
      .updateTable("work_items")
      .set({ status: "Running", updated_at: now })
      .where("id", "=", item.id)
      .execute();
    await trx
      .updateTable("launch_queue")
      .set({ state: "claimed", launch_token: launchToken })
      .where("id", "=", row.id)
      .execute();
    claims.push({
      kind: "work_item",
      workflowId,
      iterationId: item.iteration_id,
      attemptId: item.attempt_id,
      workItemId: item.id,
      agentName: item.agent_name,
      launchToken,
      queueId: row.id,
    });
  }
  return claims;
}

// --- post-commit launch guards (§2.21) --------------------------------------------

/**
 * The pre-launch recheck: the entity must still be `Running` and the queue
 * row must still carry this claim's token. A cancel, attempt failure, or
 * settlement that reached the row first makes this return false and the
 * stale launch is skipped.
 */
export async function verifyClaimLaunchable(
  db: WorkflowDb,
  claim: ClaimedLaunch,
): Promise<boolean> {
  const queueRow = await db
    .selectFrom("launch_queue")
    .select(["state", "launch_token"])
    .where("id", "=", claim.queueId)
    .executeTakeFirst();
  if (queueRow?.state !== "claimed" || queueRow.launch_token !== claim.launchToken) {
    return false;
  }
  const status =
    claim.kind === "plan"
      ? (
          await db
            .selectFrom("plans")
            .select("status")
            .where("id", "=", claim.planId)
            .executeTakeFirst()
        )?.status
      : (
          await db
            .selectFrom("work_items")
            .select("status")
            .where("id", "=", claim.workItemId)
            .executeTakeFirst()
        )?.status;
  return status === "Running";
}

/**
 * Stamp the run-to-entity binding. The port mints the run id at launch, so
 * the stamp lands immediately after `port.launch`; it is audit data and
 * stays correct even when the entity settled in the launch window.
 */
export async function stampAgentRunId(
  db: WorkflowDb,
  claim: ClaimedLaunch,
  runId: AgentRunId,
): Promise<void> {
  const now = new Date().toISOString();
  if (claim.kind === "plan") {
    await db
      .updateTable("plans")
      .set({ agent_run_id: runId, updated_at: now })
      .where("id", "=", claim.planId)
      .execute();
    return;
  }
  await db
    .updateTable("work_items")
    .set({ agent_run_id: runId, updated_at: now })
    .where("id", "=", claim.workItemId)
    .execute();
}
