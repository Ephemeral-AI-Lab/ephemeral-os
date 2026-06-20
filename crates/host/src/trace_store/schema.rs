use std::time::Duration;

use rusqlite::Connection;

use super::TraceStoreError;

pub(super) const STORE_SCHEMA_VERSION: u32 = 3;

pub(super) fn initialize(conn: &Connection) -> Result<(), TraceStoreError> {
    apply_pragmas(conn)?;
    let version: u32 = conn.pragma_query_value(None, "user_version", |row| row.get(0))?;
    if version > STORE_SCHEMA_VERSION {
        return Err(TraceStoreError::NewerSchema {
            found: version,
            supported: STORE_SCHEMA_VERSION,
        });
    }
    apply_migrations(conn, version)?;
    conn.execute_batch(DDL)?;
    conn.pragma_update(None, "user_version", STORE_SCHEMA_VERSION)?;
    Ok(())
}

fn apply_pragmas(conn: &Connection) -> Result<(), rusqlite::Error> {
    conn.busy_timeout(Duration::from_secs(30))?;
    conn.pragma_update(None, "journal_mode", "WAL")?;
    conn.pragma_update(None, "synchronous", "FULL")?;
    conn.pragma_update(None, "foreign_keys", "ON")?;
    Ok(())
}

fn apply_migrations(_conn: &Connection, _version: u32) -> Result<(), rusqlite::Error> {
    Ok(())
}

const DDL: &str = r#"
CREATE TABLE IF NOT EXISTS audit_entries (
  audit_seq             INTEGER PRIMARY KEY AUTOINCREMENT,
  sandbox_id            TEXT NOT NULL,
  trace_id              TEXT NOT NULL,
  request_id            TEXT,
  entry_kind            TEXT NOT NULL,
  schema_name           TEXT NOT NULL,
  schema_version        INTEGER NOT NULL,
  received_at_ms        INTEGER NOT NULL,
  payload               BLOB NOT NULL,
  payload_sha256        TEXT NOT NULL,
  prev_global_sha256    TEXT,
  prev_sandbox_sha256   TEXT,
  entry_sha256          TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS trace_requests (
  request_id       TEXT PRIMARY KEY,
  trace_id         TEXT NOT NULL,
  sandbox_id       TEXT NOT NULL,
  op               TEXT NOT NULL,
  caller_id        TEXT,
  args_summary     TEXT,
  args_digest      TEXT,
  workspace_route  TEXT CHECK (workspace_route IN
    ('host','isolated','fast_path','none') OR workspace_route IS NULL),
  status           TEXT,
  error_kind       TEXT,
  sent_at_ms       INTEGER NOT NULL,
  received_at_ms   INTEGER,
  host_rtt_ms      INTEGER,
  duration_us      INTEGER,
  daemon_boot_id   TEXT,
  host_boot_id     TEXT NOT NULL,
  modules_touched  TEXT,
  response_digest  TEXT,
  response_len     INTEGER,
  response_summary TEXT
);
CREATE TABLE IF NOT EXISTS trace_spans (
  trace_id        TEXT NOT NULL,
  request_id      TEXT,
  span_id         INTEGER NOT NULL,
  parent_span_id  INTEGER,
  kind            TEXT NOT NULL,
  subsystem       TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'ok',
  started_us      INTEGER NOT NULL,
  duration_us     INTEGER NOT NULL,
  fields_json     TEXT,
  PRIMARY KEY (trace_id, span_id)
);
CREATE TABLE IF NOT EXISTS trace_events (
  trace_id    TEXT NOT NULL,
  seq         INTEGER NOT NULL,
  request_id  TEXT,
  span_id     INTEGER,
  module      TEXT NOT NULL,
  event       TEXT NOT NULL,
  level       TEXT NOT NULL DEFAULT 'info',
  ts_us       INTEGER NOT NULL,
  details_json TEXT,
  PRIMARY KEY (trace_id, seq)
);
CREATE TABLE IF NOT EXISTS trace_resources (
  trace_id TEXT NOT NULL, request_id TEXT, span_id INTEGER,
  ts_us INTEGER NOT NULL, kind TEXT NOT NULL, values_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trace_links (
  trace_id  TEXT NOT NULL,
  link_kind TEXT NOT NULL,
  link_id   TEXT NOT NULL,
  request_id TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (trace_id, link_kind, link_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_audit_trace     ON audit_entries(trace_id, audit_seq);
CREATE INDEX IF NOT EXISTS idx_audit_sandbox   ON audit_entries(sandbox_id, audit_seq);
CREATE INDEX IF NOT EXISTS idx_audit_request   ON audit_entries(request_id);
CREATE INDEX IF NOT EXISTS idx_requests_trace  ON trace_requests(trace_id);
CREATE INDEX IF NOT EXISTS idx_requests_sent   ON trace_requests(sent_at_ms);
CREATE INDEX IF NOT EXISTS idx_requests_status ON trace_requests(status);
CREATE INDEX IF NOT EXISTS idx_spans_kind      ON trace_spans(kind);
CREATE INDEX IF NOT EXISTS idx_spans_request   ON trace_spans(request_id);
CREATE INDEX IF NOT EXISTS idx_events_request  ON trace_events(request_id);
CREATE INDEX IF NOT EXISTS idx_resources_trace ON trace_resources(trace_id, ts_us);
CREATE INDEX IF NOT EXISTS idx_resources_request_span_kind ON trace_resources(request_id, span_id, kind);
CREATE INDEX IF NOT EXISTS idx_links_id       ON trace_links(link_kind, link_id);
CREATE INDEX IF NOT EXISTS idx_events_span    ON trace_events(trace_id, span_id);
CREATE INDEX IF NOT EXISTS idx_events_event   ON trace_events(event);
"#;
