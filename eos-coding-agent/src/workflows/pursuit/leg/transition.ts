import {
  isPursuitEntityTerminal,
  mintLegId,
  type LegId,
  type PursuitId,
} from "../contracts/pursuit.js";
import type { LegRow, PursuitTransaction } from "../db/index.js";

import { cancelAttempt, createAttempt } from "../attempt/transition.js";
import { reconcilePursuit } from "../pursuit/transition.js";
import { decodeStringList, type PursuitTree } from "../pursuit-tree.js";

export interface CreateLegInit {
  sequence: number;
  origin: "initial" | "next_leg_goal" | "predefined";
  legGoal: string;
  legGoalProvenance: string;
  isLegGoalMutatable: boolean;
  nextLegGoal: string | null;
  maxAttempts: number;
}

export async function createLeg(
  trx: PursuitTransaction,
  pursuitId: PursuitId,
  init: CreateLegInit,
): Promise<LegId> {
  const id = mintLegId();
  const now = new Date().toISOString();
  await trx
    .insertInto("legs")
    .values({
      id,
      pursuit_id: pursuitId,
      sequence: init.sequence,
      origin: init.origin,
      leg_goal: init.legGoal,
      leg_goal_version: 1,
      leg_goal_provenance: init.legGoalProvenance,
      is_leg_goal_mutatable: init.isLegGoalMutatable ? 1 : 0,
      next_leg_goal: init.nextLegGoal,
      max_attempts: init.maxAttempts,
      status: "Running",
      created_at: now,
      updated_at: now,
    })
    .execute();
  await createAttempt(trx, { pursuitId, legId: id }, 1);
  return id;
}

export interface LegRef {
  pursuitId: PursuitId;
  legId: LegId;
}

export async function reconcileLeg(
  trx: PursuitTransaction,
  tree: PursuitTree,
  ref: LegRef,
): Promise<void> {
  const leg = await trx
    .selectFrom("legs")
    .selectAll()
    .where("id", "=", ref.legId)
    .executeTakeFirst();
  if (!leg || isPursuitEntityTerminal(leg.status)) return;

  const attempts = await trx
    .selectFrom("attempts")
    .select(["id", "sequence", "status"])
    .where("leg_id", "=", ref.legId)
    .orderBy("sequence")
    .execute();
  const closing = attempts.at(-1);
  if (!closing) return;

  const now = new Date().toISOString();
  if (closing.status === "Success") {
    await trx
      .updateTable("legs")
      .set({ status: "Success", updated_at: now })
      .where("id", "=", ref.legId)
      .execute();
    const next = await nextLegInit(trx, leg);
    if (next !== null) {
      await createLeg(trx, ref.pursuitId, next);
      return;
    }
    await reconcilePursuit(trx, tree, ref.pursuitId);
    return;
  }

  if (closing.status === "Failed" && attempts.length >= leg.max_attempts) {
    await trx
      .updateTable("legs")
      .set({ status: "Failed", updated_at: now })
      .where("id", "=", ref.legId)
      .execute();
    await reconcilePursuit(trx, tree, ref.pursuitId);
  }
}

async function nextLegInit(
  trx: PursuitTransaction,
  leg: LegRow,
): Promise<CreateLegInit | null> {
  const pursuit = await trx
    .selectFrom("pursuits")
    .select(["leg_goal_mode", "leg_goals"])
    .where("id", "=", leg.pursuit_id)
    .executeTakeFirstOrThrow();

  if (pursuit.leg_goal_mode === "dynamic") {
    if (leg.next_leg_goal === null) return null;
    return {
      sequence: leg.sequence + 1,
      origin: "next_leg_goal",
      legGoal: leg.next_leg_goal,
      legGoalProvenance: `inherited from successful leg_${String(leg.sequence)} next_leg_goal`,
      isLegGoalMutatable: true,
      nextLegGoal: null,
      maxAttempts: leg.max_attempts,
    };
  }

  const legGoals = decodeStringList(pursuit.leg_goals ?? "[]");
  const nextGoal = legGoals.at(leg.sequence);
  if (nextGoal === undefined) return null;
  return {
    sequence: leg.sequence + 1,
    origin: "predefined",
    legGoal: nextGoal,
    legGoalProvenance: `predefined leg_goal[${String(leg.sequence + 1)}]`,
    isLegGoalMutatable: false,
    nextLegGoal: legGoals[leg.sequence + 1] ?? null,
    maxAttempts: leg.max_attempts,
  };
}

export async function cancelLeg(
  trx: PursuitTransaction,
  legId: LegId,
): Promise<void> {
  const leg = await trx
    .selectFrom("legs")
    .select("status")
    .where("id", "=", legId)
    .executeTakeFirst();
  if (!leg) return;
  if (!isPursuitEntityTerminal(leg.status)) {
    await trx
      .updateTable("legs")
      .set({ status: "Cancelled", updated_at: new Date().toISOString() })
      .where("id", "=", legId)
      .execute();
  }
  const attempts = await trx
    .selectFrom("attempts")
    .select("id")
    .where("leg_id", "=", legId)
    .execute();
  for (const attempt of attempts) await cancelAttempt(trx, attempt.id);
}
