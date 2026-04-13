-- Team coordination schema (Section 14.4).
-- Run once during bootstrap. Partitions are created per-run by partitions.py.
-- No PostgreSQL extensions required (ltree/pgcrypto removed).

-- Task Center backing store
CREATE TABLE IF NOT EXISTS task_notes (
    id          UUID NOT NULL DEFAULT gen_random_uuid(),
    team_run_id TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    agent_name  TEXT NOT NULL,
    content     TEXT NOT NULL,
    scope_paths TEXT[] DEFAULT '{}',
    scope_ltree TEXT[] DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, team_run_id)
) PARTITION BY LIST (team_run_id);

-- Ledger backing store
CREATE TABLE IF NOT EXISTS file_changes (
    id          BIGSERIAL NOT NULL,
    team_run_id TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    path_ltree  TEXT NOT NULL DEFAULT '',
    agent_id    TEXT NOT NULL,
    agent_run_id TEXT DEFAULT '',
    edit_type   TEXT DEFAULT 'edit',
    old_hash    TEXT DEFAULT '',
    new_hash    TEXT DEFAULT '',
    description TEXT DEFAULT '',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, team_run_id)
) PARTITION BY LIST (team_run_id);

-- Task queue (dispatcher backing store)
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT NOT NULL,
    team_run_id     TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    task            TEXT NOT NULL,
    deps            TEXT[] DEFAULT '{}',
    scope_paths     TEXT[] DEFAULT '{}',
    scope_ltree     TEXT[] DEFAULT '{}',
    cascade_policy  TEXT DEFAULT 'cancel',
    parent_id       TEXT,
    root_id         TEXT DEFAULT '',
    depth           INT DEFAULT 0,
    pending_dep_count INT DEFAULT 0,
    retry_count     INT DEFAULT 0,
    max_retries     INT DEFAULT 2,
    agent_run_id    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    failure_reason  TEXT,
    PRIMARY KEY (id, team_run_id)
) PARTITION BY LIST (team_run_id);

-- Exploration cache (shared across runs, not partitioned)
CREATE TABLE IF NOT EXISTS exploration_memory (
    cache_key    TEXT PRIMARY KEY,
    scope_paths  TEXT[] NOT NULL,
    content_hash TEXT NOT NULL,
    notes        JSONB NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    accessed_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Checkpoint snapshots (shared across runs, not partitioned)
CREATE TABLE IF NOT EXISTS team_run_checkpoints (
    id              TEXT NOT NULL PRIMARY KEY,
    team_run_id     TEXT NOT NULL,
    sequence        INT NOT NULL,
    taken_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    label           TEXT,
    work_items      JSONB NOT NULL,
    ready_queue_order TEXT[] DEFAULT '{}',
    budget_state    JSONB NOT NULL DEFAULT '{}',
    project_context JSONB
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_run
    ON team_run_checkpoints (team_run_id, sequence DESC);

-- LISTEN/NOTIFY trigger for real-time scope change awareness (Section 14.7).
-- Fires on every INSERT into file_changes, pushing the change to all
-- listeners on the run's channel. ScopeChangeListener filters by scope
-- and routes to per-executor ScopeChangeBuffers.
CREATE OR REPLACE FUNCTION notify_scope_change() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify(
        'scope_change_' || NEW.team_run_id,
        json_build_object(
            'file_path', NEW.file_path,
            'agent_id', NEW.agent_id,
            'agent_run_id', COALESCE(NEW.agent_run_id, ''),
            'edit_type', NEW.edit_type
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_scope_change
    AFTER INSERT ON file_changes
    FOR EACH ROW EXECUTE FUNCTION notify_scope_change();

-- Indexes are created per-partition automatically when partitions are created.
-- These template indexes guide what each partition gets:
--   task_notes: (task_id), BRIN(created_at), GIN(tsvector)
--   file_changes: (path_ltree), (team_run_id, created_at DESC)
--   tasks: (team_run_id, status), (team_run_id, depth, created_at)
