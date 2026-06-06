//! Pure `read_file` helper: build payload → call transport → parse envelope.

use eos_types::SandboxId;
use serde_json::Value;

use crate::error::SandboxPortError;
use crate::models::{ReadFileRequest, ReadFileResult};
use crate::ops::DaemonOp;
use crate::timeouts::READ_FILE_TIMEOUT_S;
use crate::tool_api::parse::{daemon_request_identity_fields, parse_read_file_result};
use crate::transport::SandboxTransport;

/// Read one UTF-8 text file through the sandbox daemon.
pub async fn read_file(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &ReadFileRequest,
) -> Result<ReadFileResult, SandboxPortError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert("path".to_owned(), Value::String(request.path.clone()));
    let response = transport
        .call(sandbox_id, DaemonOp::ReadFile, payload, READ_FILE_TIMEOUT_S)
        .await?;
    parse_read_file_result(&response)
}
