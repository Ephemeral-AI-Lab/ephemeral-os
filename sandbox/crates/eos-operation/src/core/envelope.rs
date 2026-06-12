use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use eos_trace::{RequestId, SpanStatus, SpanSubsystem, SpanUid, TraceId, WorkspaceRoute};

use super::fault::OperationFault;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OperationStatus {
    Ok,
    Running,
    Rejected,
    Cancelled,
    TimedOut,
    Error,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TraceRef {
    pub trace_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub root_span_id: Option<u64>,
    /// `pending_host_ingest` daemon-side, `local_sqlite` after host ingest.
    pub store: String,
    pub event_count: u64,
    pub degraded: bool,
}

impl Default for TraceRef {
    fn default() -> Self {
        Self {
            trace_id: String::new(),
            request_id: None,
            root_span_id: None,
            store: "pending_host_ingest".to_owned(),
            event_count: 0,
            degraded: false,
        }
    }
}

impl TraceRef {
    #[must_use]
    pub fn new(trace_id: &TraceId) -> Self {
        Self {
            trace_id: trace_id.to_string(),
            ..Self::default()
        }
    }

    #[must_use]
    pub fn with_request(mut self, request_id: &RequestId) -> Self {
        self.request_id = Some(request_id.to_string());
        self
    }

    #[must_use]
    pub fn with_root_span(mut self, span_id: SpanUid) -> Self {
        self.root_span_id = Some(span_id.get());
        self
    }

    #[must_use]
    pub fn with_event_count(mut self, count: u64) -> Self {
        self.event_count = count;
        self
    }

    #[must_use]
    pub fn with_store(mut self, store: impl Into<String>) -> Self {
        self.store = store.into();
        self
    }

    #[must_use]
    pub fn degraded(mut self) -> Self {
        self.degraded = true;
        self
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OperationWarning {
    pub kind: String,
    pub message: String,
}

/// The recorded `workspace.route` decision: `{ kind, reason? }`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceRouteRef {
    pub kind: WorkspaceRoute,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

impl Default for WorkspaceRouteRef {
    fn default() -> Self {
        Self {
            kind: WorkspaceRoute::None,
            reason: None,
        }
    }
}

/// One direct child span of the request root: `{ kind, duration_us, status }`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StepSummary {
    pub kind: String,
    pub duration_us: u64,
    pub status: SpanStatus,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ResourceSummary {
    #[serde(default)]
    pub fields: Map<String, Value>,
}

/// Cross-cutting response metadata, rendered from the request's trace record —
/// never hand-inserted beside it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ResponseMeta {
    pub protocol_version: u8,
    pub op: String,
    pub request_id: String,
    pub trace: TraceRef,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub caller_id: Option<String>,
    pub workspace_route: WorkspaceRouteRef,
    pub duration_ms: f64,
    pub modules_touched: Vec<SpanSubsystem>,
    pub steps: Vec<StepSummary>,
    pub resource_summary: ResourceSummary,
    pub warnings: Vec<OperationWarning>,
}

impl Default for ResponseMeta {
    fn default() -> Self {
        Self {
            protocol_version: 2,
            op: String::new(),
            request_id: String::new(),
            trace: TraceRef::default(),
            caller_id: None,
            workspace_route: WorkspaceRouteRef::default(),
            duration_ms: 0.0,
            modules_touched: Vec::new(),
            steps: Vec::new(),
            resource_summary: ResourceSummary::default(),
            warnings: Vec::new(),
        }
    }
}

impl ResponseMeta {
    #[must_use]
    pub fn with_trace(mut self, trace: TraceRef) -> Self {
        self.trace = trace;
        self
    }
}

/// One envelope for every op. `status` is the single discriminant; arms carry
/// `result` XOR `error` by construction (`Rejected` may keep partial domain
/// facts beside its fault).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum OperationEnvelope<T> {
    Ok {
        result: T,
        meta: ResponseMeta,
    },
    /// Accepted; continues via a linked resource.
    Running {
        result: T,
        meta: ResponseMeta,
    },
    /// Finalized facts of the cancelled work.
    Cancelled {
        result: T,
        meta: ResponseMeta,
    },
    TimedOut {
        result: T,
        meta: ResponseMeta,
    },
    /// Domain refusal: OCC conflict, policy, isolated-gate. `result` keeps
    /// partial domain facts when work happened before the rejection.
    Rejected {
        error: OperationFault,
        #[serde(skip_serializing_if = "Option::is_none")]
        result: Option<T>,
        meta: ResponseMeta,
    },
    /// Parse/transport/internal/unexpected fault.
    Error {
        error: OperationFault,
        meta: ResponseMeta,
    },
}

impl<T> OperationEnvelope<T> {
    #[must_use]
    pub fn ok(result: T, meta: ResponseMeta) -> Self {
        Self::Ok { result, meta }
    }

    #[must_use]
    pub fn running(result: T, meta: ResponseMeta) -> Self {
        Self::Running { result, meta }
    }

    #[must_use]
    pub fn cancelled(result: T, meta: ResponseMeta) -> Self {
        Self::Cancelled { result, meta }
    }

    #[must_use]
    pub fn timed_out(result: T, meta: ResponseMeta) -> Self {
        Self::TimedOut { result, meta }
    }

    #[must_use]
    pub fn rejected(error: OperationFault, meta: ResponseMeta) -> Self {
        Self::Rejected {
            error,
            result: None,
            meta,
        }
    }

    #[must_use]
    pub fn rejected_with_result(error: OperationFault, result: T, meta: ResponseMeta) -> Self {
        Self::Rejected {
            error,
            result: Some(result),
            meta,
        }
    }

    #[must_use]
    pub fn error(error: OperationFault, meta: ResponseMeta) -> Self {
        Self::Error { error, meta }
    }

    #[must_use]
    pub const fn status(&self) -> OperationStatus {
        match self {
            Self::Ok { .. } => OperationStatus::Ok,
            Self::Running { .. } => OperationStatus::Running,
            Self::Cancelled { .. } => OperationStatus::Cancelled,
            Self::TimedOut { .. } => OperationStatus::TimedOut,
            Self::Rejected { .. } => OperationStatus::Rejected,
            Self::Error { .. } => OperationStatus::Error,
        }
    }

    #[must_use]
    pub const fn meta(&self) -> &ResponseMeta {
        match self {
            Self::Ok { meta, .. }
            | Self::Running { meta, .. }
            | Self::Cancelled { meta, .. }
            | Self::TimedOut { meta, .. }
            | Self::Rejected { meta, .. }
            | Self::Error { meta, .. } => meta,
        }
    }

    #[must_use]
    pub const fn fault(&self) -> Option<&OperationFault> {
        match self {
            Self::Rejected { error, .. } | Self::Error { error, .. } => Some(error),
            Self::Ok { .. }
            | Self::Running { .. }
            | Self::Cancelled { .. }
            | Self::TimedOut { .. } => None,
        }
    }

    #[must_use]
    pub const fn result(&self) -> Option<&T> {
        match self {
            Self::Ok { result, .. }
            | Self::Running { result, .. }
            | Self::Cancelled { result, .. }
            | Self::TimedOut { result, .. } => Some(result),
            Self::Rejected { result, .. } => result.as_ref(),
            Self::Error { .. } => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::core::FaultDetails;

    fn meta() -> ResponseMeta {
        ResponseMeta {
            op: "sandbox.runtime.ready".to_owned(),
            request_id: "req-1".to_owned(),
            ..ResponseMeta::default()
        }
    }

    fn six_envelopes() -> [OperationEnvelope<Value>; 6] {
        [
            OperationEnvelope::ok(json!({"ready": true}), meta()),
            OperationEnvelope::running(json!({"command_id": "cmd-1"}), meta()),
            OperationEnvelope::rejected_with_result(
                OperationFault::new("occ_conflict", "path contended"),
                json!({"exit_code": 0}),
                meta(),
            ),
            OperationEnvelope::cancelled(json!({"kill_reason": "cancelled"}), meta()),
            OperationEnvelope::timed_out(json!({"kill_reason": "timed_out"}), meta()),
            OperationEnvelope::error(
                OperationFault::internal("failed", FaultDetails::default()),
                meta(),
            ),
        ]
    }

    #[test]
    fn serializes_each_status_with_one_discriminant() {
        let statuses = six_envelopes().map(|envelope| {
            serde_json::to_value(envelope)
                .expect("envelope serializes")
                .get("status")
                .and_then(Value::as_str)
                .expect("status string")
                .to_owned()
        });
        assert_eq!(
            statuses,
            [
                "ok",
                "running",
                "rejected",
                "cancelled",
                "timed_out",
                "error"
            ]
        );
    }

    #[test]
    fn arms_carry_result_xor_error_by_construction() {
        for envelope in six_envelopes() {
            let status = envelope.status();
            let value = serde_json::to_value(&envelope).expect("envelope serializes");
            match status {
                OperationStatus::Ok
                | OperationStatus::Running
                | OperationStatus::Cancelled
                | OperationStatus::TimedOut => {
                    assert!(value.get("result").is_some(), "{status:?} carries result");
                    assert!(value.get("error").is_none(), "{status:?} has no error key");
                }
                OperationStatus::Rejected => {
                    assert!(value.get("error").is_some(), "rejected carries fault");
                    assert!(
                        value.get("result").is_some(),
                        "rejected keeps partial result facts"
                    );
                }
                OperationStatus::Error => {
                    assert!(value.get("error").is_some(), "error carries fault");
                    assert!(value.get("result").is_none(), "error has no result key");
                }
            }
            assert!(value.get("meta").is_some(), "{status:?} carries meta");
            let roundtrip: OperationEnvelope<Value> =
                serde_json::from_value(value).expect("envelope deserializes");
            assert_eq!(roundtrip.status(), status, "round trip keeps the status");
        }
    }

    #[test]
    fn meta_serializes_required_spec_fields() {
        let value = serde_json::to_value(OperationEnvelope::ok(json!({}), meta()))
            .expect("envelope serializes");
        let meta = value.get("meta").expect("meta object");
        for field in [
            "protocol_version",
            "op",
            "request_id",
            "trace",
            "workspace_route",
            "duration_ms",
            "modules_touched",
            "steps",
            "resource_summary",
            "warnings",
        ] {
            assert!(meta.get(field).is_some(), "meta.{field} is always present");
        }
        assert_eq!(meta["protocol_version"], 2);
        assert_eq!(meta["workspace_route"]["kind"], "none");
        assert_eq!(meta["trace"]["store"], "pending_host_ingest");
    }
}
