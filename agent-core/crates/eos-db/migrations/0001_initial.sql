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
    root_task_id   TEXT,
    status         TEXT NOT NULL DEFAULT 'running',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    finished_at    TEXT
);

CREATE TABLE tasks (
    id                   TEXT PRIMARY KEY,
    request_id           TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    role                 TEXT NOT NULL,
    instruction          TEXT NOT NULL,
    status               TEXT NOT NULL,
    workflow_id          TEXT,
    iteration_id         TEXT,
    attempt_id           TEXT,
    agent_name           TEXT,
    needs                TEXT NOT NULL DEFAULT '[]',
    outcomes             TEXT NOT NULL DEFAULT '[]',
    terminal_tool_result TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX ix_tasks_request_id ON tasks(request_id);
CREATE INDEX ix_tasks_workflow_id ON tasks(workflow_id);
CREATE INDEX ix_tasks_iteration_id ON tasks(iteration_id);
CREATE INDEX ix_tasks_attempt_id ON tasks(attempt_id);

CREATE TABLE workflows (
    id             TEXT PRIMARY KEY,
    request_id     TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    parent_task_id TEXT NOT NULL,
    goal           TEXT NOT NULL,
    status         TEXT NOT NULL,
    iteration_ids  TEXT NOT NULL DEFAULT '[]',
    outcomes       TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    closed_at      TEXT
);
CREATE INDEX ix_workflows_request_id ON workflows(request_id);
CREATE INDEX ix_workflows_parent_task_id ON workflows(parent_task_id);

CREATE TABLE iterations (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    sequence_no     INTEGER NOT NULL,
    creation_reason TEXT NOT NULL,
    goal            TEXT NOT NULL,
    attempt_budget  INTEGER NOT NULL,
    status          TEXT NOT NULL,
    attempt_ids     TEXT NOT NULL DEFAULT '[]',
    deferred_goal   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    closed_at       TEXT,
    outcomes        TEXT,
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
    planner_task_id     TEXT,
    generator_task_ids  TEXT NOT NULL DEFAULT '[]',
    reducer_task_ids    TEXT NOT NULL DEFAULT '[]',
    outcomes            TEXT NOT NULL DEFAULT '[]',
    deferred_goal       TEXT,
    fail_reason         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    closed_at           TEXT,
    CONSTRAINT uq_attempt_iteration_sequence UNIQUE (iteration_id, attempt_sequence_no)
);
CREATE INDEX ix_attempts_iteration_id ON attempts(iteration_id);
CREATE INDEX ix_attempts_workflow_id ON attempts(workflow_id);

CREATE TABLE agent_runs (
    id                   TEXT PRIMARY KEY,
    task_id              TEXT UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    initial_messages     TEXT,
    agent_name           TEXT NOT NULL,
    message_history      TEXT,
    terminal_tool_result TEXT,
    token_count          INTEGER NOT NULL DEFAULT 0,
    error                TEXT,
    created_at           TEXT NOT NULL,
    finished_at          TEXT
);

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
