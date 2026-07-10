use sandbox_operation_catalog::internal::runtime::SQUASH_LAYERSTACK;
use sandbox_operation_contract::{error, OperationRequest, OperationResponse, OperationScope};
use serde_json::{json, Value};

use crate::operation::ManagerServices;
use crate::router::forward_sandbox_request;

pub(crate) fn dispatch_squash_layerstacks(
    services: &ManagerServices,
    request: &OperationRequest,
) -> OperationResponse {
    let sandbox_id = match request.required_string("sandbox_id") {
        Ok(sandbox_id) => sandbox_id,
        Err(response) => return response,
    };
    let runtime_request = OperationRequest::new(
        SQUASH_LAYERSTACK,
        request.request_id.clone(),
        OperationScope::sandbox(sandbox_id),
        json!({}),
    );
    match forward_sandbox_request(services, runtime_request) {
        Ok(response) => translate_stale_daemon_response(response),
        Err(error) => error.into_response(),
    }
}

fn translate_stale_daemon_response(response: OperationResponse) -> OperationResponse {
    let value = response.into_json_value();
    if response_error_kind(&value) == Some("unknown_op") {
        return OperationResponse::fault_with_details(
            error::OPERATION_FAILED,
            "sandbox daemon does not support squash_layerstacks; recreate the sandbox so it uses the current daemon binary",
            json!({ "daemon_op": SQUASH_LAYERSTACK }),
        );
    }
    OperationResponse::ok(value)
}

fn response_error_kind(value: &Value) -> Option<&str> {
    value.get("error")?.get("kind")?.as_str()
}
