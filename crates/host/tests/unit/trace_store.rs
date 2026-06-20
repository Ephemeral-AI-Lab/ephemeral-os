use serde_json::json;
use sha2::{Digest, Sha256};
use trace::{RequestId, TraceId};

use super::audit::RESPONSE_PERSISTED_SCHEMA;
use super::*;

#[test]
fn sqlite_posture_and_schema_are_set_on_open() -> Result<(), TraceStoreError> {
    let store = temp_store("posture")?;
    let posture = store.sqlite_posture()?;

    assert_eq!(posture.journal_mode, "wal");
    assert_eq!(posture.synchronous, 2);
    assert!(store.db_path().is_file());
    Ok(())
}

#[test]
fn request_start_args_digest_excludes_the_daemon_auth_token() -> Result<(), TraceStoreError> {
    // Security rule: the auth token is never recorded, hashed, or
    // length-recorded. args_digest must describe the request args only, not the
    // forwarded TCP frame (which carries _eos_daemon_auth_token).
    let store = temp_store("args-digest-token-free")?;
    let args = json!({"caller_id": "caller-1", "path": "README.md"});
    store.append_request_start(request_input(
        "sb-1",
        "sandbox.command.count",
        false,
        "digest-token-free",
    ))?;

    let recorded = trace_request_args_digest(&store, "digest-token-free")?;
    let args_bytes = serde_json::to_vec(&args).expect("args serialize");
    assert_eq!(
        recorded,
        hex_sha256(&args_bytes),
        "args_digest must hash the args bytes only"
    );

    // A token-bearing frame must never produce the recorded digest.
    let token_frame = serde_json::to_vec(&json!({
        "op": "sandbox.command.count",
        "args": args,
        "_eos_daemon_auth_token": "super-secret-token",
    }))
    .expect("frame serialize");
    assert_ne!(
        recorded,
        hex_sha256(&token_frame),
        "args_digest must not be computed over the token-bearing frame"
    );
    Ok(())
}

#[test]
fn audit_payload_summaries_are_redacted_before_persistence() -> Result<(), TraceStoreError> {
    let store = temp_store("redacted-summaries")?;
    let trace_id = TraceId::parse("trace-redacted").expect("trace id");
    let request_id = RequestId::parse("request-redacted").expect("request id");
    store.append_request_start(RequestStartInput {
        sandbox_id: "sb-1",
        trace_id: trace_id.clone(),
        request_id: request_id.clone(),
        op: "sandbox.command.exec",
        caller_id: Some("caller-1"),
        mutates_state: true,
        args: json!({
            "caller_id": "caller-1",
            "cmd": "printenv",
            "api_key": "sk-live",
            "nested": {"password": "pw"},
        }),
    })?;
    store.append_trace_event(TraceEventInput {
        sandbox_id: "sb-1",
        trace_id: &trace_id,
        request_id: Some(&request_id),
        span_id: None,
        module: "host.transport",
        event: "request_written",
        details: json!({"Authorization": "Bearer token", "bytes": 12}),
    })?;
    store.record_response_persisted(ResponsePersistedInput {
        sandbox_id: "sb-1",
        trace_id: &trace_id,
        request_id: &request_id,
        response: &json!({
            "status": "ok",
            "result": {"token": "result-token", "safe": "visible"},
            "meta": {},
        }),
        raw_response_bytes:
            br#"{"status":"ok","result":{"token":"result-token","safe":"visible"},"meta":{}}"#,
        host_rtt_ms: 1,
    })?;

    let (args_summary, response_summary): (String, String) = store.lock().query_row(
        "SELECT args_summary, response_summary FROM trace_requests WHERE request_id=?1",
        [request_id.as_str()],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    assert!(args_summary.contains("[redacted]"), "{args_summary}");
    assert!(!args_summary.contains("sk-live"), "{args_summary}");
    assert!(!args_summary.contains("\"pw\""), "{args_summary}");
    assert!(
        response_summary.contains("[redacted]"),
        "{response_summary}"
    );
    assert!(response_summary.contains("visible"), "{response_summary}");
    assert!(
        !response_summary.contains("result-token"),
        "{response_summary}"
    );

    let details_json: String = store.lock().query_row(
        "SELECT details_json FROM trace_events WHERE request_id=?1 AND event='request_written'",
        [request_id.as_str()],
        |row| row.get(0),
    )?;
    assert!(details_json.contains("[redacted]"), "{details_json}");
    assert!(!details_json.contains("Bearer token"), "{details_json}");
    Ok(())
}

#[test]
fn trace_event_append_failures_record_a_durable_loss_entry() -> Result<(), TraceStoreError> {
    let store = temp_store("trace-event-loss")?;
    let trace_id = TraceId::parse("trace-event-loss").expect("trace id");
    let request_id = RequestId::parse("request-event-loss").expect("request id");
    store.fail_next_trace_event_for_tests();

    let error = store
        .append_trace_event_or_loss(TraceEventInput {
            sandbox_id: "sb-1",
            trace_id: &trace_id,
            request_id: Some(&request_id),
            span_id: None,
            module: "host.protocol",
            event: "request_written",
            details: json!({"bytes": 12}),
        })
        .expect_err("injected event append failure should reach caller");
    assert!(
        matches!(error, TraceStoreError::InjectedTraceEventFailure),
        "{error}"
    );

    let payload = trace_loss_payload(&store, trace_id.as_str())?;
    assert_eq!(payload["reason"], "trace_event_append_failed");
    assert_eq!(payload["trace_id"], trace_id.as_str());
    assert_eq!(payload["request_id"], request_id.as_str());
    assert_eq!(payload["module"], "host.protocol");
    assert_eq!(payload["event"], "request_written");
    assert!(
        payload["message"]
            .as_str()
            .is_some_and(|message| message.contains("trace event append intentionally failed")),
        "{payload}"
    );
    Ok(())
}

#[test]
fn request_start_failures_fail_closed_for_mutations_and_degrade_reads(
) -> Result<(), TraceStoreError> {
    let store = temp_store("fail-closed")?;

    store.fail_next_request_start_for_tests();
    let mutating = store.prepare_forward(request_input(
        "sb-1",
        "sandbox.command.exec",
        true,
        "write-1",
    ));
    assert!(matches!(
        mutating,
        Err(TraceStoreError::InjectedRequestStartFailure)
    ));

    store.fail_next_request_start_for_tests();
    let read = store.prepare_forward(request_input(
        "sb-1",
        "sandbox.command.count",
        false,
        "read-1",
    ))?;
    assert!(read.degraded);

    let degraded_count: i64 = store.lock().query_row(
        "SELECT COUNT(*) FROM audit_entries WHERE entry_kind='trace_degraded'",
        [],
        |row| row.get(0),
    )?;
    assert_eq!(degraded_count, 1);
    let degraded_row = store
        .request_by_id("read-1")?
        .expect("degraded read request row");
    assert_eq!(degraded_row.sandbox_id, "sb-1");
    assert_eq!(degraded_row.status.as_deref(), Some("trace_degraded"));
    Ok(())
}

#[test]
fn response_finalization_and_host_events_rebuild_from_audit_entries() -> Result<(), TraceStoreError>
{
    let store = temp_store("response-finalization")?;
    let request = request_input("sb-1", "sandbox.command.count", false, "request-finalized");
    let trace_id = request.trace_id.clone();
    let request_id = request.request_id.clone();
    store.append_request_start(request)?;

    store.append_trace_event(TraceEventInput {
        sandbox_id: "sb-1",
        trace_id: &trace_id,
        request_id: Some(&request_id),
        span_id: None,
        module: "host.transport",
        event: "connect_failed",
        details: json!({"endpoint": "127.0.0.1:9", "error_kind": "connect_failed"}),
    })?;
    let response = json!({"status": "ok", "result": {"content": "ok"}, "meta": {}});
    let raw = br#"{"status":"ok","result":{"content":"ok"},"meta":{},"_trace_events":"encoded"}"#;
    store.record_response_persisted(ResponsePersistedInput {
        sandbox_id: "sb-1",
        trace_id: &trace_id,
        request_id: &request_id,
        response: &response,
        raw_response_bytes: raw,
        host_rtt_ms: 17,
    })?;

    let row = store
        .request_by_id(request_id.as_str())?
        .expect("request row");
    assert_eq!(row.status.as_deref(), Some("ok"));
    assert_eq!(store.events_for_trace(trace_id.as_str())?.len(), 1);
    let (schema_name, payload): (String, Vec<u8>) = store.lock().query_row(
        "SELECT schema_name, payload FROM audit_entries WHERE entry_kind='response_persisted'",
        [],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    assert_eq!(schema_name, RESPONSE_PERSISTED_SCHEMA);
    let decoded = proto::ResponsePersisted::decode(payload.as_slice())?;
    assert_eq!(decoded.trace_id, trace_id.as_str());
    assert_eq!(decoded.request_id, request_id.as_str());
    assert_eq!(decoded.status, "ok");
    assert!(decoded.error_kind.is_empty());
    assert_eq!(decoded.host_rtt_ms, 17);
    assert_eq!(decoded.response_len, raw.len() as u64);
    assert!(decoded.response_summary_json.contains("\"content\":\"ok\""));

    store.record_response_missing(ResponseMissingInput {
        sandbox_id: "sb-1",
        trace_id: &trace_id,
        request_id: &request_id,
        status: "uncertain",
        error_kind: "read_timeout",
        message: "daemon response timed out",
    })?;
    let missing = store
        .request_by_id(request_id.as_str())?
        .expect("missing response row");
    assert_eq!(missing.status.as_deref(), Some("uncertain"));
    Ok(())
}

#[test]
fn response_projection_classifies_new_envelope_errors() -> Result<(), TraceStoreError> {
    let store = temp_store("response-envelope-error")?;
    let request = request_input(
        "sb-1",
        "sandbox.command.count",
        false,
        "request-envelope-error",
    );
    let trace_id = request.trace_id.clone();
    let request_id = request.request_id.clone();
    store.append_request_start(request)?;

    let response = json!({
        "status": "error",
        "error": {
            "kind": "internal_error",
            "message": "failed",
            "details": {}
        },
        "meta": {}
    });
    let raw = br#"{"status":"error","error":{"kind":"internal_error","message":"failed","details":{}},"meta":{}}"#;
    store.record_response_persisted(ResponsePersistedInput {
        sandbox_id: "sb-1",
        trace_id: &trace_id,
        request_id: &request_id,
        response: &response,
        raw_response_bytes: raw,
        host_rtt_ms: 3,
    })?;

    let row = store
        .request_by_id(request_id.as_str())?
        .expect("request row");
    assert_eq!(row.status.as_deref(), Some("error"));
    assert_eq!(
        trace_request_error_kind(&store, request_id.as_str())?.as_deref(),
        Some("internal_error")
    );

    Ok(())
}

#[test]
fn audit_verifier_accepts_intact_store_and_reports_tampering() -> Result<(), TraceStoreError> {
    let store = temp_store("audit-verify")?;
    let request = request_input("sb-1", "sandbox.command.count", false, "verify-request");
    let trace_id = request.trace_id.clone();
    let request_id = request.request_id.clone();
    store.append_request_start(request)?;

    store.record_response_persisted(ResponsePersistedInput {
        sandbox_id: "sb-1",
        trace_id: &trace_id,
        request_id: &request_id,
        response: &json!({"status": "ok", "result": {}, "meta": {}}),
        raw_response_bytes: br#"{"status":"ok","result":{},"meta":{}}"#,
        host_rtt_ms: 1,
    })?;

    let report = store.verify_audit(Some(trace_id.as_str()))?;
    assert!(report.ok, "intact audit store verifies: {report:?}");
    assert_eq!(report.first_error, None);
    assert_eq!(report.scope, "global_chain_with_trace_projection");
    assert_eq!(
        report.checked_entries, 3,
        "trace-scoped verification still checks the global hash chain"
    );

    store.lock().execute(
        "UPDATE audit_entries SET payload=x'00' WHERE request_id=?1 AND entry_kind='response_persisted'",
        [request_id.as_str()],
    )?;
    let tampered = store.verify_audit(Some(trace_id.as_str()))?;
    assert_eq!(
        verify_error_kind(&tampered).as_deref(),
        Some("payload_hash_mismatch")
    );
    Ok(())
}

#[test]
fn audit_verifier_reports_chain_and_projection_failures() -> Result<(), TraceStoreError> {
    let store = temp_store("audit-verify-failures")?;
    let request = request_input("sb-1", "sandbox.command.count", false, "verify-chain");
    let trace_id = request.trace_id.clone();
    store.append_request_start(request)?;

    store.lock().execute(
        "UPDATE audit_entries SET prev_global_sha256='wrong' WHERE trace_id=?1 AND entry_kind='request_start'",
        [trace_id.as_str()],
    )?;
    let broken_chain = store.verify_audit(Some(trace_id.as_str()))?;
    assert_eq!(
        verify_error_kind(&broken_chain).as_deref(),
        Some("global_chain_mismatch")
    );

    let store = temp_store("audit-verify-projection")?;
    let request = request_input("sb-1", "sandbox.command.count", false, "verify-projection");
    let trace_id = request.trace_id.clone();
    store.append_request_start(request)?;
    store.lock().execute(
        "DELETE FROM trace_requests WHERE request_id='verify-projection'",
        [],
    )?;
    let projection_gap = store.verify_audit(Some(trace_id.as_str()))?;
    assert_eq!(
        verify_error_kind(&projection_gap).as_deref(),
        Some("projection_missing_request")
    );
    Ok(())
}

#[test]
fn startup_reconciles_prior_boot_incomplete_requests_to_uncertain() -> Result<(), TraceStoreError> {
    let dir = temp_dir("reconcile");
    {
        let store = TraceStore::open(&dir)?;
        store.append_request_start(request_input(
            "sb-1",
            "sandbox.command.exec",
            true,
            "orphan",
        ))?;
    }

    let reopened = TraceStore::open(&dir)?;
    let request = reopened
        .request_by_id("orphan")?
        .expect("orphan request exists");
    assert_eq!(request.status.as_deref(), Some("uncertain"));
    Ok(())
}

#[test]
fn newer_schema_versions_are_refused() -> Result<(), TraceStoreError> {
    let dir = temp_dir("newer-version");
    std::fs::create_dir_all(&dir).expect("create temp dir");
    let db = dir.join("sandbox-traces.sqlite");
    let conn = rusqlite::Connection::open(db)?;
    conn.pragma_update(None, "user_version", 999_u32)?;
    drop(conn);

    assert!(matches!(
        TraceStore::open(&dir),
        Err(TraceStoreError::NewerSchema { found: 999, .. })
    ));
    Ok(())
}

fn request_input<'a>(
    sandbox_id: &'a str,
    op: &'a str,
    mutates_state: bool,
    request_id: &'a str,
) -> RequestStartInput<'a> {
    RequestStartInput {
        sandbox_id,
        trace_id: TraceId::parse(format!("trace-{request_id}")).expect("trace id"),
        request_id: RequestId::parse(request_id).expect("request id"),
        op,
        caller_id: Some("caller-1"),
        mutates_state,
        args: json!({"caller_id": "caller-1", "path": "README.md"}),
    }
}

fn temp_store(name: &str) -> Result<TraceStore, TraceStoreError> {
    TraceStore::open(temp_dir(name))
}

fn trace_request_args_digest(
    store: &TraceStore,
    request_id: &str,
) -> Result<String, TraceStoreError> {
    Ok(store.lock().query_row(
        "SELECT args_digest FROM trace_requests WHERE request_id=?1",
        [request_id],
        |row| row.get(0),
    )?)
}

fn hex_sha256(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut out = String::with_capacity(digest.len() * 2);
    for byte in digest {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

fn trace_request_error_kind(
    store: &TraceStore,
    request_id: &str,
) -> Result<Option<String>, TraceStoreError> {
    Ok(store.lock().query_row(
        "SELECT error_kind FROM trace_requests WHERE request_id=?1",
        [request_id],
        |row| row.get(0),
    )?)
}

fn verify_error_kind(report: &TraceVerifyReport) -> Option<String> {
    report.first_error.as_ref().map(|error| error.kind.clone())
}

fn trace_loss_payload(
    store: &TraceStore,
    trace_id: &str,
) -> Result<serde_json::Value, TraceStoreError> {
    let payload: Vec<u8> = store.lock().query_row(
        "SELECT payload FROM audit_entries
         WHERE entry_kind='loss' AND trace_id=?1
         ORDER BY audit_seq DESC LIMIT 1",
        [trace_id],
        |row| row.get(0),
    )?;
    let entry = proto::AuditEntry::decode(payload.as_slice())?;
    Ok(serde_json::from_slice(&entry.payload).expect("loss payload is json"))
}

fn temp_dir(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "eos-host-trace-store-{name}-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&dir);
    dir
}
