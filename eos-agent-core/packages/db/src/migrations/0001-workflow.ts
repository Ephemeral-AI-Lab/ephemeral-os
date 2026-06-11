import type { Database } from "better-sqlite3";

/**
 * The one workflow migration. Plans and work items are rows, not an
 * execution-tree JSON; iterations carry no goal/focus columns (derived
 * views over append-only plan declarations); the launch queue carries the
 * claim token the post-commit launcher rechecks.
 */
export function applyWorkflowMigration(database: Database): void {
  database.exec(`
    CREATE TABLE IF NOT EXISTS workflows (
      id TEXT PRIMARY KEY,
      parent_run_id TEXT NOT NULL,
      original_goal TEXT NOT NULL,
      status TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      closed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS iterations (
      id TEXT PRIMARY KEY,
      workflow_id TEXT NOT NULL REFERENCES workflows(id),
      sequence INTEGER NOT NULL,
      origin TEXT NOT NULL CHECK (origin IN ('initial', 'deferred_goal')),
      max_attempts INTEGER NOT NULL,
      status TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE (workflow_id, sequence)
    );

    CREATE TABLE IF NOT EXISTS attempts (
      id TEXT PRIMARY KEY,
      workflow_id TEXT NOT NULL REFERENCES workflows(id),
      iteration_id TEXT NOT NULL REFERENCES iterations(id),
      sequence INTEGER NOT NULL,
      status TEXT NOT NULL,
      fail_reason TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE (iteration_id, sequence)
    );

    CREATE TABLE IF NOT EXISTS plans (
      id TEXT PRIMARY KEY,
      workflow_id TEXT NOT NULL REFERENCES workflows(id),
      iteration_id TEXT NOT NULL REFERENCES iterations(id),
      attempt_id TEXT NOT NULL REFERENCES attempts(id),
      agent_run_id TEXT,
      status TEXT NOT NULL,
      declared_focus TEXT,
      declared_deferred_goal TEXT,
      planner_summary TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS work_items (
      id TEXT PRIMARY KEY,
      workflow_id TEXT NOT NULL REFERENCES workflows(id),
      iteration_id TEXT NOT NULL REFERENCES iterations(id),
      attempt_id TEXT NOT NULL REFERENCES attempts(id),
      plan_id TEXT NOT NULL REFERENCES plans(id),
      agent_name TEXT NOT NULL,
      agent_run_id TEXT,
      status TEXT NOT NULL,
      description TEXT NOT NULL,
      work_item_spec TEXT NOT NULL,
      needs TEXT NOT NULL,
      worker_summary TEXT,
      worker_outcome TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS launch_queue (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      workflow_id TEXT NOT NULL REFERENCES workflows(id),
      kind TEXT NOT NULL CHECK (kind IN ('plan', 'work_item')),
      entity_id TEXT NOT NULL,
      state TEXT NOT NULL CHECK (state IN ('queued', 'claimed')),
      launch_token TEXT,
      created_at TEXT NOT NULL
    );
  `);
}
