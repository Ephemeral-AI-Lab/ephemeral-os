import SQLite from "better-sqlite3";
import { Kysely, SqliteDialect } from "kysely";

import { applyWorkflowMigration } from "./migrations/0001-workflow.js";
import type { WorkflowDatabase } from "./schema.js";

export type WorkflowDb = Kysely<WorkflowDatabase>;

/**
 * Open (or create) the workflow database at `path` (`":memory:"` for
 * tests), apply the migration, and return the Kysely handle. better-sqlite3
 * is synchronous and single-connection, so concurrent transactions
 * serialize at the driver.
 */
export function createWorkflowDatabase(path: string): WorkflowDb {
  const database = new SQLite(path);
  database.pragma("journal_mode = WAL");
  database.pragma("foreign_keys = ON");
  applyWorkflowMigration(database);
  return new Kysely<WorkflowDatabase>({
    dialect: new SqliteDialect({ database }),
  });
}
