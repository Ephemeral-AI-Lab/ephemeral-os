import type { PursuitId } from "../../contracts/pursuit.js";
import { sql, type Kysely, type Transaction } from "kysely";

import type {
  AttemptRow,
  LegRow,
  PlanRow,
  WorkItemRow,
  PursuitDatabase,
  PursuitRow,
} from "./schema.js";

/** Plain handle or open transaction; loads see the transaction's own writes. */
export type PursuitDbReader = Kysely<PursuitDatabase>;

export type PursuitTransaction = Transaction<PursuitDatabase>;

/** One pursuit's rows, deterministically ordered, ready for tree derivation. */
export interface PursuitRows {
  pursuit: PursuitRow;
  legs: LegRow[];
  attempts: AttemptRow[];
  plans: PlanRow[];
  workItems: WorkItemRow[];
}

/**
 * The row-shaped load `loadPursuitTree` consumes. Returns undefined when
 * the pursuit does not exist; never derives - derivation is `@eos/pursuit`.
 */
export async function loadPursuitRows(
  db: PursuitDbReader,
  pursuitId: PursuitId,
): Promise<PursuitRows | undefined> {
  const pursuit = await db
    .selectFrom("pursuits")
    .selectAll()
    .where("id", "=", pursuitId)
    .executeTakeFirst();
  if (!pursuit) return undefined;
  const [legs, attempts, plans, workItems] = await Promise.all([
    db
      .selectFrom("legs")
      .selectAll()
      .where("pursuit_id", "=", pursuitId)
      .orderBy("sequence")
      .execute(),
    db
      .selectFrom("attempts")
      .selectAll()
      .where("pursuit_id", "=", pursuitId)
      .orderBy("sequence")
      .execute(),
    db
      .selectFrom("plans")
      .selectAll()
      .where("pursuit_id", "=", pursuitId)
      .orderBy(sql`rowid`)
      .execute(),
    db
      .selectFrom("work_items")
      .selectAll()
      .where("pursuit_id", "=", pursuitId)
      .orderBy(sql`rowid`)
      .execute(),
  ]);
  return { pursuit, legs, attempts, plans, workItems };
}
