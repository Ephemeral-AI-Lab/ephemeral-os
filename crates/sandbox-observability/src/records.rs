use thiserror::Error;

const MAX_ID_LENGTH: usize = 256;
const MAX_KIND_LENGTH: usize = 64;
const MAX_STATUS_LENGTH: usize = 64;
const MAX_OPERATION_LENGTH: usize = 128;
const MAX_METHOD_LENGTH: usize = 256;
const MAX_ERROR_KIND_LENGTH: usize = 128;
const MAX_ERROR_MESSAGE_LENGTH: usize = 4096;
const MAX_SNAPSHOT_STATE_LENGTH: usize = 64;

#[derive(Debug, Error)]
pub enum RecordValidationError {
    #[error("{field} is empty")]
    Empty { field: &'static str },
    #[error("{field} exceeds {max_len} bytes")]
    TooLong { field: &'static str, max_len: usize },
    #[error("span trace_id {span_trace_id} does not match trace_id {trace_id}")]
    SpanTraceMismatch {
        trace_id: String,
        span_trace_id: String,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub struct TraceRecord {
    pub trace_id: String,
    pub kind: String,
    pub status: String,
    pub sandbox_id: String,
    pub operation: String,
    pub request_id: Option<String>,
    pub started_at_unix_ms: i64,
    pub finished_at_unix_ms: Option<i64>,
    pub duration_ms: Option<f64>,
    pub error_kind: Option<String>,
    pub error_message: Option<String>,
}

impl TraceRecord {
    pub(crate) fn validate(&self) -> Result<(), RecordValidationError> {
        validate_required("trace_id", &self.trace_id, MAX_ID_LENGTH)?;
        validate_required("kind", &self.kind, MAX_KIND_LENGTH)?;
        validate_required("status", &self.status, MAX_STATUS_LENGTH)?;
        validate_required("sandbox_id", &self.sandbox_id, MAX_ID_LENGTH)?;
        validate_required("operation", &self.operation, MAX_OPERATION_LENGTH)?;
        validate_optional("request_id", self.request_id.as_deref(), MAX_ID_LENGTH)?;
        validate_optional(
            "error_kind",
            self.error_kind.as_deref(),
            MAX_ERROR_KIND_LENGTH,
        )?;
        validate_optional(
            "error_message",
            self.error_message.as_deref(),
            MAX_ERROR_MESSAGE_LENGTH,
        )?;

        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct SpanRecord {
    pub span_id: String,
    pub trace_id: String,
    pub parent_span_id: Option<String>,
    pub method_name: String,
    pub call_index: i64,
    pub status: String,
    pub started_at_unix_ms: i64,
    pub finished_at_unix_ms: Option<i64>,
    pub duration_ms: Option<f64>,
    pub error_kind: Option<String>,
    pub error_message: Option<String>,
}

impl SpanRecord {
    pub(crate) fn validate_for_trace(&self, trace_id: &str) -> Result<(), RecordValidationError> {
        validate_required("span_id", &self.span_id, MAX_ID_LENGTH)?;
        validate_required("trace_id", &self.trace_id, MAX_ID_LENGTH)?;
        validate_optional(
            "parent_span_id",
            self.parent_span_id.as_deref(),
            MAX_ID_LENGTH,
        )?;
        validate_required("method_name", &self.method_name, MAX_METHOD_LENGTH)?;
        validate_required("status", &self.status, MAX_STATUS_LENGTH)?;
        validate_optional(
            "error_kind",
            self.error_kind.as_deref(),
            MAX_ERROR_KIND_LENGTH,
        )?;
        validate_optional(
            "error_message",
            self.error_message.as_deref(),
            MAX_ERROR_MESSAGE_LENGTH,
        )?;

        if self.trace_id != trace_id {
            return Err(RecordValidationError::SpanTraceMismatch {
                trace_id: trace_id.to_owned(),
                span_trace_id: self.trace_id.clone(),
            });
        }

        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SandboxSnapshotRecord {
    pub sandbox_id: String,
    pub state: String,
    pub sampled_at_unix_ms: i64,
    pub error_message: Option<String>,
}

impl SandboxSnapshotRecord {
    pub(crate) fn validate(&self) -> Result<(), RecordValidationError> {
        validate_required("sandbox_id", &self.sandbox_id, MAX_ID_LENGTH)?;
        validate_required("state", &self.state, MAX_SNAPSHOT_STATE_LENGTH)?;
        validate_optional(
            "error_message",
            self.error_message.as_deref(),
            MAX_ERROR_MESSAGE_LENGTH,
        )?;

        Ok(())
    }
}

fn validate_required(
    field: &'static str,
    value: &str,
    max_len: usize,
) -> Result<(), RecordValidationError> {
    if value.is_empty() {
        return Err(RecordValidationError::Empty { field });
    }

    validate_len(field, value, max_len)
}

fn validate_optional(
    field: &'static str,
    value: Option<&str>,
    max_len: usize,
) -> Result<(), RecordValidationError> {
    if let Some(value) = value {
        validate_len(field, value, max_len)?;
    }

    Ok(())
}

fn validate_len(
    field: &'static str,
    value: &str,
    max_len: usize,
) -> Result<(), RecordValidationError> {
    if value.len() > max_len {
        return Err(RecordValidationError::TooLong { field, max_len });
    }

    Ok(())
}
