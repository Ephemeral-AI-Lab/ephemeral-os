use rusqlite::{params, Transaction};

use super::payload::{HostTraceEventPayload, ResponsePersistedPayload, TraceDegradedPayload};
use super::u64_to_i64;

pub(super) struct ProjectRequestStart<'a> {
    pub(super) sandbox_id: &'a str,
    pub(super) trace_id: &'a str,
    pub(super) request_id: &'a str,
    pub(super) op: &'a str,
    pub(super) caller_id: Option<&'a str>,
    pub(super) args_summary: &'a str,
    pub(super) args_digest: &'a str,
    pub(super) sent_at_ms: u64,
    pub(super) host_boot_id: &'a str,
}

pub(super) fn project_request_start_tx(
    tx: &Transaction<'_>,
    row: ProjectRequestStart<'_>,
) -> Result<(), rusqlite::Error> {
    tx.execute(
        "INSERT OR REPLACE INTO trace_requests
         (request_id, trace_id, sandbox_id, op, caller_id, args_summary,
          args_digest, sent_at_ms, host_boot_id)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![
            row.request_id,
            row.trace_id,
            row.sandbox_id,
            row.op,
            row.caller_id,
            row.args_summary,
            row.args_digest,
            row.sent_at_ms,
            row.host_boot_id,
        ],
    )?;
    Ok(())
}

pub(super) fn project_trace_degraded_tx(
    tx: &Transaction<'_>,
    payload: &TraceDegradedPayload,
) -> Result<(), rusqlite::Error> {
    project_request_start_tx(
        tx,
        ProjectRequestStart {
            sandbox_id: &payload.sandbox_id,
            trace_id: &payload.trace_id,
            request_id: &payload.request_id,
            op: &payload.op,
            caller_id: payload.caller_id.as_deref(),
            args_summary: &payload.args_summary,
            args_digest: &payload.args_digest,
            sent_at_ms: payload.sent_at_ms,
            host_boot_id: &payload.host_boot_id,
        },
    )?;
    tx.execute(
        "UPDATE trace_requests
         SET status='trace_degraded', error_kind=?2, response_summary=?3
         WHERE request_id=?1",
        params![payload.request_id, payload.error_kind, payload.message],
    )?;
    Ok(())
}

pub(super) fn project_host_trace_event_tx(
    tx: &Transaction<'_>,
    payload: &HostTraceEventPayload,
) -> Result<(), rusqlite::Error> {
    let seq = next_trace_seq(tx, &payload.trace_id)?;
    tx.execute(
        "INSERT INTO trace_events
         (trace_id, seq, request_id, span_id, module, event, level, ts_us, details_json)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'info', ?7, ?8)",
        params![
            payload.trace_id,
            seq,
            payload.request_id,
            payload.span_id,
            payload.module,
            payload.event,
            u64_to_i64(payload.ts_us),
            payload.details_json,
        ],
    )?;
    Ok(())
}

pub(super) fn project_response_persisted_tx(
    tx: &Transaction<'_>,
    payload: &ResponsePersistedPayload,
) -> Result<(), rusqlite::Error> {
    tx.execute(
        "UPDATE trace_requests
         SET status=?2, error_kind=?3, received_at_ms=?4, host_rtt_ms=?5,
             response_digest=?6, response_len=?7, response_summary=?8
         WHERE request_id=?1",
        params![
            payload.request_id,
            payload.status,
            payload.error_kind,
            payload.received_at_ms,
            payload.host_rtt_ms,
            payload.response_digest,
            payload.response_len,
            payload.response_summary,
        ],
    )?;
    Ok(())
}

fn next_trace_seq(tx: &Transaction<'_>, trace_id: &str) -> Result<i64, rusqlite::Error> {
    tx.query_row(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM trace_events WHERE trace_id=?1",
        params![trace_id],
        |row| row.get(0),
    )
}
