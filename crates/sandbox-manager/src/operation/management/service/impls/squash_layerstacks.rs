use sandbox_protocol::{error_kind, CliOperationScope, Request, Response};
use serde_json::{json, Value};

use crate::operation::ManagerServices;
use crate::router::forward_sandbox_request;

const RUNTIME_SQUASH_OP: &str = "squash_layerstack";

pub(crate) fn dispatch_squash_layerstacks(
    services: &ManagerServices,
    request: &Request,
) -> Response {
    let sandbox_id = match request.required_string("sandbox_id") {
        Ok(sandbox_id) => sandbox_id,
        Err(response) => return response,
    };
    let runtime_request = Request::new(
        RUNTIME_SQUASH_OP,
        request.request_id.clone(),
        CliOperationScope::sandbox(sandbox_id),
        json!({}),
    );
    match forward_sandbox_request(services, runtime_request) {
        Ok(response) => translate_stale_daemon_response(response),
        Err(error) => error.into_response(),
    }
}

fn translate_stale_daemon_response(response: Response) -> Response {
    let value = response.into_json_value();
    if response_error_kind(&value) == Some("unknown_op") {
        return Response::fault_with_details(
            error_kind::OPERATION_FAILED,
            "sandbox daemon does not support squash_layerstacks; recreate the sandbox so it uses the current daemon binary",
            json!({ "daemon_op": RUNTIME_SQUASH_OP }),
        );
    }
    Response::ok(value)
}

fn response_error_kind(value: &Value) -> Option<&str> {
    value.get("error")?.get("kind")?.as_str()
}
