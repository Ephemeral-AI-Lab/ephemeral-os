//! Live `get_observability` view router. Serves runtime-derived views (currently
//! `layerstack`) without reading the NDJSON log; the SQLite snapshot path stays
//! on its own private op.

use sandbox_observability::sample_layerstack;
use sandbox_protocol::{error_kind, Request, Response};
use sandbox_runtime::SandboxRuntimeOperations;

use super::layerstack::layerstack_view_value;

pub(crate) fn observability_view_response(
    operations: &SandboxRuntimeOperations,
    request: &Request,
) -> Response {
    let view = match request.optional_string("view") {
        Ok(view) => view,
        Err(response) => return response,
    };
    match view.as_deref() {
        Some("layerstack") => layerstack_view_response(operations),
        Some(other) => Response::fault(
            error_kind::INVALID_REQUEST,
            format!("unsupported observability view: {other}"),
        ),
        None => Response::fault(
            error_kind::INVALID_REQUEST,
            "observability request requires a view".to_owned(),
        ),
    }
}

fn layerstack_view_response(operations: &SandboxRuntimeOperations) -> Response {
    let observation = match operations.observe_layerstack() {
        Ok(observation) => observation,
        Err(error) => {
            return Response::fault(
                error_kind::INTERNAL_ERROR,
                format!("layerstack observe failed: {error}"),
            )
        }
    };
    let bytes = sample_layerstack(operations.layer_stack_root());
    Response::ok(layerstack_view_value(&observation, &bytes))
}
