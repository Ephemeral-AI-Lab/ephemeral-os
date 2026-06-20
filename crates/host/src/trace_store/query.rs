//! Read-side projection/query over the trace store sqlite schema. These free
//! functions operate on a borrowed `Connection` and are delegated to by the
//! public read methods on `TraceStore` so callers keep a single type.

#[cfg(any(test, feature = "e2e-support"))]
use rusqlite::OptionalExtension;
use rusqlite::{params, Connection};

use super::TraceStoreError;

#[cfg(any(test, feature = "e2e-support"))]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SqlitePosture {
    pub journal_mode: String,
    pub synchronous: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct TraceEventRow {
    pub seq: i64,
    pub module: String,
    pub event: String,
    pub details_json: String,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct TraceRequestRow {
    pub request_id: String,
    pub trace_id: String,
    pub sandbox_id: String,
    pub op: String,
    pub caller_id: Option<String>,
    pub args_summary: Option<String>,
    pub args_digest: Option<String>,
    pub status: Option<String>,
    pub error_kind: Option<String>,
    pub sent_at_ms: i64,
    pub received_at_ms: Option<i64>,
    pub host_rtt_ms: Option<i64>,
    pub host_boot_id: String,
    pub response_digest: Option<String>,
    pub response_len: Option<i64>,
    pub response_summary: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct TraceAuditEntryRow {
    pub audit_seq: i64,
    pub sandbox_id: String,
    pub trace_id: String,
    pub request_id: Option<String>,
    pub entry_kind: String,
    pub schema_name: String,
    pub schema_version: i64,
    pub received_at_ms: i64,
    pub payload_sha256: String,
    pub prev_global_sha256: Option<String>,
    pub prev_sandbox_sha256: Option<String>,
    pub entry_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct AuditVerificationRow {
    pub audit_seq: i64,
    pub sandbox_id: String,
    pub trace_id: String,
    pub request_id: Option<String>,
    pub entry_kind: String,
    pub schema_name: String,
    pub schema_version: i64,
    pub received_at_ms: i64,
    pub payload: Vec<u8>,
    pub payload_sha256: String,
    pub prev_global_sha256: Option<String>,
    pub prev_sandbox_sha256: Option<String>,
    pub entry_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct ProjectionGap {
    pub audit_seq: i64,
    pub request_id: String,
}

const TRACE_REQUEST_COLUMNS: &str = "\
request_id, trace_id, sandbox_id, op, caller_id, args_summary,
args_digest, status, error_kind, sent_at_ms, received_at_ms,
host_rtt_ms, host_boot_id, response_digest, response_len, response_summary";

#[cfg(any(test, feature = "e2e-support"))]
pub(super) fn events_for_trace(
    conn: &Connection,
    trace_id: &str,
) -> Result<Vec<TraceEventRow>, TraceStoreError> {
    events_for_trace_limited(conn, trace_id, usize::MAX)
}

pub(super) fn events_for_trace_limited(
    conn: &Connection,
    trace_id: &str,
    limit: usize,
) -> Result<Vec<TraceEventRow>, TraceStoreError> {
    let limit = i64::try_from(limit).unwrap_or(i64::MAX);
    let mut stmt = conn.prepare(
        "SELECT seq, module, event, details_json FROM trace_events
         WHERE trace_id=?1 ORDER BY seq
         LIMIT ?2",
    )?;
    let rows = stmt
        .query_map(params![trace_id, limit], |row| {
            Ok(TraceEventRow {
                seq: row.get(0)?,
                module: row.get(1)?,
                event: row.get(2)?,
                details_json: row.get(3)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

#[cfg(any(test, feature = "e2e-support"))]
pub(super) fn request_by_id(
    conn: &Connection,
    request_id: &str,
) -> Result<Option<TraceRequestRow>, TraceStoreError> {
    let sql = format!("SELECT {TRACE_REQUEST_COLUMNS} FROM trace_requests WHERE request_id=?1");
    conn.query_row(&sql, params![request_id], trace_request_row)
        .optional()
        .map_err(TraceStoreError::from)
}

pub(super) fn recent_requests(
    conn: &Connection,
    sandbox_id: Option<&str>,
    limit: usize,
) -> Result<Vec<TraceRequestRow>, TraceStoreError> {
    let limit = i64::try_from(limit).unwrap_or(i64::MAX);
    let sql = format!(
        "SELECT {TRACE_REQUEST_COLUMNS}
         FROM trace_requests
         WHERE (?1 IS NULL OR sandbox_id=?1)
         ORDER BY sent_at_ms DESC, request_id DESC
         LIMIT ?2"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt
        .query_map(params![sandbox_id, limit], trace_request_row)?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

pub(super) fn requests_for_trace_limited(
    conn: &Connection,
    trace_id: &str,
    limit: usize,
) -> Result<Vec<TraceRequestRow>, TraceStoreError> {
    let limit = i64::try_from(limit).unwrap_or(i64::MAX);
    let sql = format!(
        "SELECT {TRACE_REQUEST_COLUMNS}
         FROM trace_requests
         WHERE trace_id=?1
         ORDER BY sent_at_ms, request_id
         LIMIT ?2"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt
        .query_map(params![trace_id, limit], trace_request_row)?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

pub(super) fn audit_entries_for_trace_limited(
    conn: &Connection,
    trace_id: &str,
    limit: usize,
) -> Result<Vec<TraceAuditEntryRow>, TraceStoreError> {
    let limit = i64::try_from(limit).unwrap_or(i64::MAX);
    let mut stmt = conn.prepare(
        "SELECT audit_seq, sandbox_id, trace_id, request_id, entry_kind, schema_name,
                schema_version, received_at_ms, payload_sha256, prev_global_sha256,
                prev_sandbox_sha256, entry_sha256
         FROM audit_entries
         WHERE trace_id=?1
         ORDER BY audit_seq
         LIMIT ?2",
    )?;
    let rows = stmt
        .query_map(params![trace_id, limit], |row| {
            Ok(TraceAuditEntryRow {
                audit_seq: row.get(0)?,
                sandbox_id: row.get(1)?,
                trace_id: row.get(2)?,
                request_id: row.get(3)?,
                entry_kind: row.get(4)?,
                schema_name: row.get(5)?,
                schema_version: row.get(6)?,
                received_at_ms: row.get(7)?,
                payload_sha256: row.get(8)?,
                prev_global_sha256: row.get(9)?,
                prev_sandbox_sha256: row.get(10)?,
                entry_sha256: row.get(11)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

pub(super) fn audit_rows_for_verification(
    conn: &Connection,
) -> Result<Vec<AuditVerificationRow>, TraceStoreError> {
    let mut stmt = conn.prepare(
        "SELECT audit_seq, sandbox_id, trace_id, request_id, entry_kind, schema_name,
                schema_version, received_at_ms, payload, payload_sha256,
                prev_global_sha256, prev_sandbox_sha256, entry_sha256
         FROM audit_entries
         ORDER BY audit_seq",
    )?;
    let rows = stmt
        .query_map([], |row| {
            Ok(AuditVerificationRow {
                audit_seq: row.get(0)?,
                sandbox_id: row.get(1)?,
                trace_id: row.get(2)?,
                request_id: row.get(3)?,
                entry_kind: row.get(4)?,
                schema_name: row.get(5)?,
                schema_version: row.get(6)?,
                received_at_ms: row.get(7)?,
                payload: row.get(8)?,
                payload_sha256: row.get(9)?,
                prev_global_sha256: row.get(10)?,
                prev_sandbox_sha256: row.get(11)?,
                entry_sha256: row.get(12)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

pub(super) fn projection_gaps(
    conn: &Connection,
    trace_id: Option<&str>,
) -> Result<Vec<ProjectionGap>, TraceStoreError> {
    let mut stmt = conn.prepare(
        "SELECT a.audit_seq, a.request_id
         FROM audit_entries a
         LEFT JOIN trace_requests r ON r.request_id=a.request_id
         WHERE a.request_id IS NOT NULL
           AND a.entry_kind IN ('request_start', 'trace_degraded', 'response_persisted')
           AND r.request_id IS NULL
           AND (?1 IS NULL OR a.trace_id=?1)
         ORDER BY a.audit_seq",
    )?;
    let rows = stmt
        .query_map(params![trace_id], |row| {
            Ok(ProjectionGap {
                audit_seq: row.get(0)?,
                request_id: row.get(1)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

#[cfg(any(test, feature = "e2e-support"))]
pub(super) fn sqlite_posture(conn: &Connection) -> Result<SqlitePosture, TraceStoreError> {
    let journal_mode: String = conn.pragma_query_value(None, "journal_mode", |row| row.get(0))?;
    let synchronous: i64 = conn.pragma_query_value(None, "synchronous", |row| row.get(0))?;
    Ok(SqlitePosture {
        journal_mode,
        synchronous,
    })
}

fn trace_request_row(row: &rusqlite::Row<'_>) -> Result<TraceRequestRow, rusqlite::Error> {
    Ok(TraceRequestRow {
        request_id: row.get(0)?,
        trace_id: row.get(1)?,
        sandbox_id: row.get(2)?,
        op: row.get(3)?,
        caller_id: row.get(4)?,
        args_summary: row.get(5)?,
        args_digest: row.get(6)?,
        status: row.get(7)?,
        error_kind: row.get(8)?,
        sent_at_ms: row.get(9)?,
        received_at_ms: row.get(10)?,
        host_rtt_ms: row.get(11)?,
        host_boot_id: row.get(12)?,
        response_digest: row.get(13)?,
        response_len: row.get(14)?,
        response_summary: row.get(15)?,
    })
}
