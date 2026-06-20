//! Cross-layer response-envelope `meta` contract.
//!
//! The daemon stamps this on every real operation response, and the gateway
//! synthesizes the same shape for gate-level rejections that never reach the
//! engine. Owning it here keeps the two producers byte-identical on the wire.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::fault::OperationFault;

/// Wire schema version stamped on every response `meta`.
pub const ENVELOPE_VERSION: u8 = 2;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OperationWarning {
    pub kind: String,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ResourceSummary {
    #[serde(default)]
    pub fields: Map<String, Value>,
}

/// Cross-cutting response metadata shared by daemon, host, and gateway envelopes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ResponseMeta {
    pub envelope_version: u8,
    pub op: String,
    pub request_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub caller_id: Option<String>,
    pub duration_ms: f64,
    pub resource_summary: ResourceSummary,
    pub warnings: Vec<OperationWarning>,
}

impl Default for ResponseMeta {
    fn default() -> Self {
        Self {
            envelope_version: ENVELOPE_VERSION,
            op: String::new(),
            request_id: String::new(),
            caller_id: None,
            duration_ms: 0.0,
            resource_summary: ResourceSummary::default(),
            warnings: Vec::new(),
        }
    }
}

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

/// One wire envelope for every op. `status` is the single discriminant; arms
/// carry `result` XOR `error` by construction (`Rejected` may keep partial
/// domain facts beside its fault).
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
