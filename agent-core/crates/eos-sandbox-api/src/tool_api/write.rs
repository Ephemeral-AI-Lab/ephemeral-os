//! Pure `write_file` helper: build payload → call transport → parse envelope.

use eos_types::SandboxId;
use serde_json::Value;

use crate::error::SandboxApiError;
use crate::models::{WriteFileRequest, WriteFileResult};
use crate::ops::DaemonOp;
use crate::timeouts::WRITE_FILE_TIMEOUT_S;
use crate::tool_api::parse::{daemon_request_identity_fields, parse_write_file_result};
use crate::transport::SandboxTransport;

/// Write one UTF-8 file through sandbox-local OCC.
pub async fn write_file(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &WriteFileRequest,
) -> Result<WriteFileResult, SandboxApiError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert("path".to_owned(), Value::String(request.path.clone()));
    payload.insert("content".to_owned(), Value::String(request.content.clone()));
    payload.insert(
        "description".to_owned(),
        Value::String(
            request
                .base
                .description_or(&format!("write {}", request.path)),
        ),
    );
    payload.insert("overwrite".to_owned(), Value::Bool(request.overwrite));
    let response = transport
        .call(
            sandbox_id,
            DaemonOp::WriteFile,
            payload,
            WRITE_FILE_TIMEOUT_S,
        )
        .await?;
    parse_write_file_result(&response)
}
