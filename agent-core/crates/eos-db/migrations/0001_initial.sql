-- Sole authoritative schema for agent-core (impl-eos-db.md §8, GC-eos-db-05).
-- Final column names (no legacy rename/drop path — that runtime DDL patching is
-- dropped). Timestamps are stored as TEXT (sqlx `time` encodes OffsetDateTime as
-- an RFC3339-ish string). JSON columns are TEXT-of-validated-JSON. FK cascades
-- are enforced because `pool.rs` sets `PRAGMA foreign_keys = ON` per connection.

CREATE TABLE requests (
    id             TEXT PRIMARY KEY,
    cwd            TEXT NOT NULL,
    sandbox_id     TEXT,
    request_prompt TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'running',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    finished_at    TEXT
);

CREATE TABLE workflows (
    id             TEXT PRIMARY KEY,
    request_id     TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    parent_agent_run_id TEXT NOT NULL,
    tool_use_id    TEXT,
    workflow_goal  TEXT NOT NULL,
    status         TEXT NOT NULL,
    iteration_ids  TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    closed_at      TEXT
);
CREATE INDEX ix_workflows_request_id ON workflows(request_id);
CREATE INDEX ix_workflows_parent_agent_run_id ON workflows(parent_agent_run_id);

CREATE TABLE agent_runs (
    agent_run_id     TEXT PRIMARY KEY,
    request_id        TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    agent_type        TEXT NOT NULL CHECK (agent_type IN ('agent', 'subagent', 'advisor')),
    status            TEXT NOT NULL,
    agent_name        TEXT NOT NULL,
    parent_agent_run_id TEXT,
    tool_use_id       TEXT,
    terminal_payload  TEXT,
    task_outcome      TEXT,
    token_count       INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    finished_at       TEXT
);
CREATE INDEX ix_agent_runs_request_id ON agent_runs(request_id);
CREATE INDEX ix_agent_runs_parent_agent_run_id ON agent_runs(parent_agent_run_id);

CREATE TABLE iterations (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    sequence_no     INTEGER NOT NULL,
    creation_reason TEXT NOT NULL,
    workflow_goal   TEXT NOT NULL,
    iteration_goal  TEXT NOT NULL,
    attempt_budget  INTEGER NOT NULL,
    status          TEXT NOT NULL,
    attempt_ids     TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    closed_at       TEXT,
    CONSTRAINT uq_iteration_workflow_sequence UNIQUE (workflow_id, sequence_no)
);
CREATE INDEX ix_iterations_workflow_id ON iterations(workflow_id);

CREATE TABLE attempts (
    id                  TEXT PRIMARY KEY,
    iteration_id        TEXT NOT NULL REFERENCES iterations(id) ON DELETE CASCADE,
    workflow_id         TEXT NOT NULL,
    attempt_sequence_no INTEGER NOT NULL,
    stage               TEXT NOT NULL,
    status              TEXT NOT NULL,
    plan_id             TEXT NOT NULL,
    execution_tree      TEXT NOT NULL DEFAULT '{}',
    fail_reason         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    closed_at           TEXT,
    CONSTRAINT uq_attempt_iteration_sequence UNIQUE (iteration_id, attempt_sequence_no)
);
CREATE INDEX ix_attempts_iteration_id ON attempts(iteration_id);
CREATE INDEX ix_attempts_workflow_id ON attempts(workflow_id);

CREATE TABLE model_registrations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    class_path  TEXT NOT NULL,
    kwargs_json TEXT NOT NULL DEFAULT '{}',
    is_active   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
