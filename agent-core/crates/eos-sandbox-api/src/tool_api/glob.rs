//! Pure `glob` helper: build payload → call transport → parse envelope.

use eos_types::SandboxId;
use serde_json::Value;

use crate::error::SandboxApiError;
use crate::models::{GlobRequest, GlobResult};
use crate::ops::DaemonOp;
use crate::timeouts::GLOB_TIMEOUT_S;
use crate::tool_api::parse::{daemon_request_identity_fields, parse_glob_result};
use crate::transport::SandboxTransport;

/// Enumerate workspace paths matching `request.pattern`.
pub async fn glob(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &GlobRequest,
) -> Result<GlobResult, SandboxApiError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert("pattern".to_owned(), Value::String(request.pattern.clone()));
    if let Some(path) = &request.path {
        payload.insert("path".to_owned(), Value::String(path.clone()));
    }
    let response = transport
        .call(sandbox_id, DaemonOp::Glob, payload, GLOB_TIMEOUT_S)
        .await?;
    parse_glob_result(&response)
}
