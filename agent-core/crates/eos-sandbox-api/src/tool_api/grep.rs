//! Pure `grep` helper: build payload → call transport → parse envelope.

use eos_types::SandboxId;
use serde_json::Value;

use crate::error::SandboxApiError;
use crate::models::{GrepRequest, GrepResult};
use crate::ops::DaemonOp;
use crate::timeouts::GREP_TIMEOUT_S;
use crate::tool_api::parse::{daemon_request_identity_fields, parse_grep_result};
use crate::transport::SandboxTransport;

/// Regex-scan workspace file contents under the sandbox's leased snapshot.
pub async fn grep(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &GrepRequest,
) -> Result<GrepResult, SandboxApiError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert("pattern".to_owned(), Value::String(request.pattern.clone()));
    payload.insert(
        "output_mode".to_owned(),
        Value::String(request.output_mode.clone()),
    );
    payload.insert("offset".to_owned(), Value::from(request.offset));
    payload.insert(
        "case_insensitive".to_owned(),
        Value::Bool(request.case_insensitive),
    );
    payload.insert("line_numbers".to_owned(), Value::Bool(request.line_numbers));
    payload.insert("multiline".to_owned(), Value::Bool(request.multiline));
    if let Some(path) = &request.path {
        payload.insert("path".to_owned(), Value::String(path.clone()));
    }
    if let Some(glob_filter) = &request.glob_filter {
        payload.insert("glob_filter".to_owned(), Value::String(glob_filter.clone()));
    }
    if let Some(head_limit) = request.head_limit {
        payload.insert("head_limit".to_owned(), Value::from(head_limit));
    }
    let response = transport
        .call(sandbox_id, DaemonOp::Grep, payload, GREP_TIMEOUT_S)
        .await?;
    parse_grep_result(&response)
}
