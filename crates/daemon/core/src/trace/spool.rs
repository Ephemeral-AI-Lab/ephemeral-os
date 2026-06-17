use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use trace::{
    BootId, EventRecord, ExportId, SpanKind, SpanRecord, SpanStatus, SpanUid, TraceExportBatch,
    TraceId, TraceKind, TraceRecord, TraceSpool,
};

static CONNECTION_SEQ: AtomicU64 = AtomicU64::new(1);
static BACKGROUND_SPOOL: OnceLock<Mutex<TraceSpool>> = OnceLock::new();
static DAEMON_BOOT_ID: OnceLock<BootId> = OnceLock::new();

pub(crate) fn daemon_boot_id() -> &'static BootId {
    DAEMON_BOOT_ID.get_or_init(BootId::new)
}

#[derive(Debug, Clone)]
pub(crate) struct RequestTraceFacts {
    pub connection_id: String,
    pub accepted_at_unix_ms: u64,
    pub listener_kind: &'static str,
    pub peer_addr: Option<String>,
    pub local_addr: Option<String>,
    pub is_tcp: bool,
    pub request_bytes: usize,
    pub read_duration_us: u64,
    pub auth_required: bool,
    pub auth_ok: bool,
    pub protocol_version: Option<i64>,
}

#[derive(Debug, Clone)]
pub(crate) struct RequestTraceEvent {
    pub(crate) span_id: SpanUid,
    pub(crate) name: String,
    pub(crate) module: String,
    pub(crate) details: Value,
}

impl RequestTraceEvent {
    pub(crate) fn operation(
        module: impl Into<String>,
        name: impl Into<String>,
        details: Value,
    ) -> Self {
        Self {
            span_id: SpanUid::new(4),
            name: name.into(),
            module: module.into(),
            details,
        }
    }
}

#[derive(Debug, Clone, Default)]
pub(crate) struct RequestTraceEventSink {
    events: Arc<Mutex<Vec<RequestTraceEvent>>>,
}

impl RequestTraceEventSink {
    pub(crate) fn push(&self, event: RequestTraceEvent) {
        if let Ok(mut events) = self.events.lock() {
            events.push(event);
        }
    }

    pub(crate) fn drain(&self) -> Vec<RequestTraceEvent> {
        self.events
            .lock()
            .map_or_else(|_| Vec::new(), |mut events| events.drain(..).collect())
    }
}

pub(crate) fn next_connection_id() -> String {
    format!(
        "daemon-conn-{}",
        CONNECTION_SEQ.fetch_add(1, Ordering::Relaxed)
    )
}

pub(crate) fn push_background_record(record: TraceRecord) {
    let Ok(mut spool) = background_spool().lock() else {
        return;
    };
    let _ = spool.push(record);
}

pub(crate) fn lease_background_records(max_records: usize) -> TraceExportBatch {
    let Ok(mut spool) = background_spool().lock() else {
        return empty_trace_export_batch();
    };
    spool.lease_batch(max_records, Some(daemon_boot_id().to_string()))
}

pub(crate) fn ack_background_export(
    export_id: &ExportId,
    batch_sha256: &str,
    record_count: usize,
) -> bool {
    background_spool()
        .lock()
        .is_ok_and(|mut spool| spool.ack_batch(export_id, batch_sha256, record_count))
}

fn empty_trace_export_batch() -> TraceExportBatch {
    TraceExportBatch {
        export_id: None,
        record_count: 0,
        spool_pending_after: 0,
        dropped_traces: 0,
        batch_sha256: None,
        trace_batch_bytes: None,
    }
}

pub(crate) fn idle_workspace_evict_record(
    report: &crate::workspace_runtime::IdleWorkspaceEvictionReport,
) -> TraceRecord {
    let now = now_ms();
    let mut span = SpanRecord::new(
        SpanUid::ROOT,
        None,
        "workspace.idle.evict",
        SpanKind::IsolatedNetwork,
        json!({
            "evicted_count": report.evicted.len(),
        }),
    );
    span.started_at_unix_ms = now;
    span.finished_at_unix_ms = now;
    span.status = Some(SpanStatus::Ok);

    let mut record = TraceRecord::new(TraceId::new(), SpanUid::ROOT);
    record.kind = TraceKind::IdleWorkspaceEvict;
    record.started_at_unix_ms = now;
    record.finished_at_unix_ms = now;
    record.spans.push(span);
    for eviction in &report.evicted {
        let mut event = EventRecord::new(
            SpanUid::ROOT,
            "workspace_evicted",
            "isolated_network",
            json!({
                "caller_id": eviction.caller_id,
                "workspace_handle_id": eviction.workspace_handle_id,
                "lease_id": eviction.lease_id,
                "evicted_upperdir_bytes": eviction.evicted_upperdir_bytes,
                "lifetime_s": eviction.lifetime_s,
                "total_ms": eviction.total_ms,
                "lease_released": eviction.lease_release.released,
                "lease_release_error": eviction.lease_release.error,
                "active_leases_after": eviction.active_leases_after,
            }),
        );
        event.at_unix_ms = now;
        record.events.push(event);
    }
    record
}

fn background_spool() -> &'static Mutex<TraceSpool> {
    BACKGROUND_SPOOL.get_or_init(|| Mutex::new(TraceSpool::default()))
}

pub(crate) fn now_ms() -> u64 {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    u64::try_from(millis).unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use trace::{SpanKind, TraceKind};

    use super::*;

    #[test]
    fn request_trace_event_sink_degrades_when_lock_is_poisoned() {
        let sink = RequestTraceEventSink::default();
        let poisoned = sink.clone();
        let _ = std::panic::catch_unwind(move || {
            let _guard = poisoned.events.lock().expect("trace sink lock");
            panic!("poison trace sink");
        });

        sink.push(RequestTraceEvent::operation(
            "trace.test",
            "after_poison",
            serde_json::json!({}),
        ));

        assert!(
            sink.drain().is_empty(),
            "poisoned trace sink should drop events instead of panicking"
        );
    }

    #[test]
    fn idle_workspace_evict_record_carries_evicted_workspace_facts() {
        let report = crate::workspace_runtime::IdleWorkspaceEvictionReport {
            evicted: vec![crate::workspace_runtime::IdleWorkspaceEviction {
                caller_id: "caller".to_owned(),
                workspace_handle_id: "workspace-handle".to_owned(),
                lease_id: "lease-1".to_owned(),
                evicted_upperdir_bytes: 4096,
                lifetime_s: 12.5,
                total_ms: 3.0,
                lease_release: crate::workspace_runtime::LeaseReleaseReport {
                    released: Some(true),
                    error: None,
                },
                active_leases_after: 0,
            }],
        };

        let record = idle_workspace_evict_record(&report);

        assert_eq!(record.kind, TraceKind::IdleWorkspaceEvict);
        assert_eq!(record.spans[0].kind, SpanKind::IsolatedNetwork);
        let event = record.events.first().expect("eviction event");
        assert_eq!(event.module, "isolated_network");
        assert_eq!(event.name, "workspace_evicted");
        assert_eq!(event.details.value["caller_id"], "caller");
        assert_eq!(
            event.details.value["workspace_handle_id"],
            "workspace-handle"
        );
        assert_eq!(event.details.value["lease_released"], true);
    }
}
