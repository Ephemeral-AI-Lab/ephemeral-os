import SQLite from "better-sqlite3";
import { Kysely, SqliteDialect } from "kysely";

import { applyPursuitMigration } from "./migrations/0001-pursuit.js";
import type { PursuitDatabase } from "./schema.js";

export type PursuitDb = Kysely<PursuitDatabase>;

/**
 * Open (or create) the pursuit database at `path` (`":memory:"` for
 * tests), apply the migration, and return the Kysely handle. better-sqlite3
 * is synchronous and single-connection, so concurrent transactions
 * serialize at the driver.
 */
export function createPursuitDatabase(path: string): PursuitDb {
  const database = new SQLite(path);
  database.pragma("journal_mode = WAL");
  database.pragma("foreign_keys = ON");
  applyPursuitMigration(database);
  return new Kysely<PursuitDatabase>({
    dialect: new SqliteDialect({ database }),
  });
}
