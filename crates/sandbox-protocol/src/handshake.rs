use sandbox_operation_contract::{OperationRequest, OperationScope};

use crate::{encode_authenticated_request_line, DAEMON_AUTH_FIELD};

pub const DAEMON_READINESS_OPERATION: &str = "sandbox_daemon_ready";
pub const DAEMON_READINESS_REQUEST_ID: &str = "docker-readiness";

pub fn daemon_readiness_request_line(
    sandbox_id: &str,
    auth_token: &str,
) -> Result<Vec<u8>, serde_json::Error> {
    encode_authenticated_request_line(
        &OperationRequest::new(
            DAEMON_READINESS_OPERATION,
            DAEMON_READINESS_REQUEST_ID,
            OperationScope::sandbox(sandbox_id),
            serde_json::json!({}),
        ),
        DAEMON_AUTH_FIELD,
        auth_token,
    )
}
