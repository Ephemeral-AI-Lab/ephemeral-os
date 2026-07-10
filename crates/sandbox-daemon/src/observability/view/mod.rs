use sandbox_operation_contract::{error, OperationRequest, OperationResponse};
use sandbox_runtime::SandboxRuntimeOperations;

use super::adapter::DaemonObservabilityAdapter;
use super::DaemonObservability;

pub(crate) fn observability_view_response(
    operations: &SandboxRuntimeOperations,
    observability: Option<&DaemonObservability>,
    request: &OperationRequest,
) -> OperationResponse {
    let view = match request.optional_string("view") {
        Ok(view) => view,
        Err(response) => return response,
    };
    match view.as_deref() {
        Some(view @ ("snapshot" | "trace" | "events" | "cgroup" | "layerstack")) => {
            let request = OperationRequest::new(
                view,
                request.request_id.clone(),
                request.scope.clone(),
                request.args.clone(),
            );
            sandbox_observability_application::dispatch_operation(
                &DaemonObservabilityAdapter::new(operations, observability),
                &request,
            )
        }
        Some(other) => OperationResponse::fault(
            error::INVALID_REQUEST,
            format!("unsupported observability view: {other}"),
        ),
        None => OperationResponse::fault(
            error::INVALID_REQUEST,
            "observability request requires a view".to_owned(),
        ),
    }
}
