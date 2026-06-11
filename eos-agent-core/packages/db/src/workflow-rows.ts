import type { WorkflowId } from "@eos/contracts";
import { sql, type Kysely, type Transaction } from "kysely";

import type {
  AttemptRow,
  IterationRow,
  PlanRow,
  WorkItemRow,
  WorkflowDatabase,
  WorkflowRow,
} from "./schema.js";

/** Plain handle or open transaction; loads see the transaction's own writes. */
export type WorkflowDbReader = Kysely<WorkflowDatabase>;

export type WorkflowTransaction = Transaction<WorkflowDatabase>;

/** One workflow's rows, deterministically ordered, ready for tree derivation. */
export interface WorkflowRows {
  workflow: WorkflowRow;
  iterations: IterationRow[];
  attempts: AttemptRow[];
  plans: PlanRow[];
  workItems: WorkItemRow[];
}

/**
 * The row-shaped load `loadWorkflowTree` consumes. Returns undefined when
 * the workflow does not exist; never derives - derivation is `@eos/workflow`.
 */
export async function loadWorkflowRows(
  db: WorkflowDbReader,
  workflowId: WorkflowId,
): Promise<WorkflowRows | undefined> {
  const workflow = await db
    .selectFrom("workflows")
    .selectAll()
    .where("id", "=", workflowId)
    .executeTakeFirst();
  if (!workflow) return undefined;
  const [iterations, attempts, plans, workItems] = await Promise.all([
    db
      .selectFrom("iterations")
      .selectAll()
      .where("workflow_id", "=", workflowId)
      .orderBy("sequence")
      .execute(),
    db
      .selectFrom("attempts")
      .selectAll()
      .where("workflow_id", "=", workflowId)
      .orderBy("sequence")
      .execute(),
    db
      .selectFrom("plans")
      .selectAll()
      .where("workflow_id", "=", workflowId)
      .orderBy(sql`rowid`)
      .execute(),
    db
      .selectFrom("work_items")
      .selectAll()
      .where("workflow_id", "=", workflowId)
      .orderBy(sql`rowid`)
      .execute(),
  ]);
  return { workflow, iterations, attempts, plans, workItems };
}
