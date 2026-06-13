import { randomUUID } from "node:crypto";

import {
  createAgentOutcomeFn,
  type Agent,
  type AgentOutcomeFn,
  type AgentRunId,
} from "eos-agent-sdk";

import {
  isPursuitEntityTerminal,
  planIdFrom,
  PlannerOutcomePayloadSchema,
  WorkerOutcomePayloadSchema,
  type AttemptId,
  type LegId,
  type PlanId,
  type PlannerOutcomePayload,
  type PlannerSubmissionTarget,
  type PursuitId,
  type WorkerOutcomePayload,
  type WorkerSubmissionTarget,
  type WorkItemId,
} from "../contracts/pursuit.js";
import type { LaunchQueueRow, PursuitDb, PursuitTransaction } from "../db/src/index.js";

import type { PursuitService } from "./service.js";
import { workItemReady } from "./work-item/state.js";

// --- agent slice + terminal outcome factories (spec §11) -------------------------

/**
 * The narrow agent capability pursuit consumes: create an SDK agent bound to a
 * terminal outcome and start it. The host adapts `AgentFactory` to this slice
 * (and wraps advisory) before handing it in; pursuit never sees host vocabulary.
 */
export interface PursuitAgents {
  create<T>(agentName: string, outcome: AgentOutcomeFn<T>): Agent<T>;
}

const SUBMIT_PLANNER_DESCRIPTION =
  "Finish the planner run by submitting its leg plan: a one-paragraph summary, " +
  "optional leg_goal/next_leg_goal, and the leg's work items.";
const SUBMIT_WORKER_DESCRIPTION =
  "Finish the worker run by submitting the assigned work item's result: a " +
  "summary, the is_pass verdict, and the outcome text.";

/**
 * Planner terminal contract: an accepted submission routes straight to the
 * service's single-mutator writer; a correctable error reopens the run.
 */
export function plannerOutcome(
  service: Pick<PursuitService, "submitPlannerOutcome">,
  target: PlannerSubmissionTarget,
): AgentOutcomeFn<PlannerOutcomePayload> {
  return createAgentOutcomeFn({
    name: "submit_planner_outcome",
    description: SUBMIT_PLANNER_DESCRIPTION,
    schema: PlannerOutcomePayloadSchema,
    onSubmit: async (payload, ctx) => {
      const result = await service.submitPlannerOutcome({
        target,
        payload,
        runId: ctx.runId,
        submissionId: ctx.submissionId,
      });
      return result.ok ? { accept: payload } : { reject: result.error };
    },
  });
}

export function workerOutcome(
  service: Pick<PursuitService, "submitWorkerOutcome">,
  target: WorkerSubmissionTarget,
): AgentOutcomeFn<WorkerOutcomePayload> {
  return createAgentOutcomeFn({
    name: "submit_worker_outcome",
    description: SUBMIT_WORKER_DESCRIPTION,
    schema: WorkerOutcomePayloadSchema,
    onSubmit: async (payload, ctx) => {
      const result = await service.submitWorkerOutcome({
        target,
        payload,
        runId: ctx.runId,
        submissionId: ctx.submissionId,
      });
      return result.ok ? { accept: payload } : { reject: result.error };
    },
  });
}

// --- launch queue ----------------------------------------------------------------

export async function enqueueLaunch(
  trx: PursuitTransaction,
  pursuitId: PursuitId,
  kind: "plan" | "work_item",
  entityId: string,
): Promise<void> {
  await trx
    .insertInto("launch_queue")
    .values({
      pursuit_id: pursuitId,
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
      pursuitId: PursuitId;
      legId: LegId;
      attemptId: AttemptId;
      planId: PlanId;
      agentName: string;
      launchToken: string;
      queueId: number;
    }
  | {
      kind: "work_item";
      pursuitId: PursuitId;
      legId: LegId;
      attemptId: AttemptId;
      workItemKey: string;
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
 * `depends_on` targets are all `Success`. Nothing launches here - the post-commit
 * launcher does, after rechecking the token.
 */
export async function claimLaunchable(
  trx: PursuitTransaction,
  pursuitId: PursuitId,
  plannerAgentName: string,
): Promise<ClaimedLaunch[]> {
  const pursuit = await trx
    .selectFrom("pursuits")
    .select("status")
    .where("id", "=", pursuitId)
    .executeTakeFirst();
  if (pursuit?.status !== "Running") return [];

  const queued = await trx
    .selectFrom("launch_queue")
    .selectAll()
    .where("pursuit_id", "=", pursuitId)
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
      if (!attempt || isPursuitEntityTerminal(attempt.status)) continue;
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
        pursuitId,
        legId: plan.leg_id,
        attemptId: plan.attempt_id,
        planId: plan.id,
        agentName: plannerAgentName,
        launchToken,
        queueId: row.id,
      });
      continue;
    }

    claims.push(...(await claimReadyWorkItems(trx, pursuitId, [row], now)));
  }
  return claims;
}

async function claimReadyWorkItems(
  trx: PursuitTransaction,
  pursuitId: PursuitId,
  queuedRows: readonly LaunchQueueRow[],
  now = new Date().toISOString(),
): Promise<Extract<ClaimedLaunch, { kind: "work_item" }>[]> {
  const claims: Extract<ClaimedLaunch, { kind: "work_item" }>[] = [];
  for (const row of queuedRows) {
    if (row.kind !== "work_item") continue;
    const item = await trx
      .selectFrom("work_items")
      .selectAll()
      .where("key", "=", row.entity_id)
      .executeTakeFirst();
    if (item?.status !== "NotStarted") continue;
    const attempt = await trx
      .selectFrom("attempts")
      .select("status")
      .where("id", "=", item.attempt_id)
      .executeTakeFirst();
    if (attempt?.status !== "Running") continue;
    if (!(await directDependenciesSucceeded(trx, item))) continue;

    const launchToken = randomUUID();
    await trx
      .updateTable("work_items")
      .set({ status: "Running", updated_at: now })
      .where("key", "=", item.key)
      .execute();
    await trx
      .updateTable("launch_queue")
      .set({ state: "claimed", launch_token: launchToken })
      .where("id", "=", row.id)
      .execute();
    claims.push({
      kind: "work_item",
      pursuitId,
      legId: item.leg_id,
      attemptId: item.attempt_id,
      workItemKey: item.key,
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
  db: PursuitDb,
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
  if (claim.kind === "plan") {
    const status = (
      await db
        .selectFrom("plans")
        .select("status")
        .where("id", "=", claim.planId)
        .executeTakeFirst()
    )?.status;
    return status === "Running";
  }
  const item = await db
    .selectFrom("work_items")
    .selectAll()
    .where("key", "=", claim.workItemKey)
    .executeTakeFirst();
  return item?.status === "Running" && (await directDependenciesSucceeded(db, item));
}

async function directDependenciesSucceeded(
  db: PursuitDb | PursuitTransaction,
  item: {
    leg_id: LegId;
    leg_goal_version: number;
    depends_on: string;
  },
): Promise<boolean> {
  const dependencies = decodeDependsOn(item.depends_on);
  if (dependencies.length === 0) return true;
  const rows = await db
    .selectFrom("work_items")
    .select(["id", "status"])
    .where("leg_id", "=", item.leg_id)
    .where("leg_goal_version", "=", item.leg_goal_version)
    .execute();
  const statusOf = new Map(rows.map((row) => [String(row.id), row.status]));
  return workItemReady(dependencies, (id) => statusOf.get(id));
}

function decodeDependsOn(raw: string): string[] {
  const parsed: unknown = JSON.parse(raw);
  return Array.isArray(parsed) ? parsed.map(String) : [];
}

/**
 * Stamp the run-to-entity binding. The run id is minted at `start`, so the
 * stamp lands immediately after launch; it is audit data and stays correct
 * even when the entity settled in the launch window.
 */
export async function stampAgentRunId(
  db: PursuitDb,
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
    .where("key", "=", claim.workItemKey)
    .execute();
}
