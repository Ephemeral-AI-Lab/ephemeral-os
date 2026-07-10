//! Live `get_observability` view router. Serves every view from live runtime
//! state plus the leaf `Reader` over the one NDJSON log — no storage engine.
//! One submodule per view owns that operation's request parsing and rendering.

mod cgroup;
mod events;
mod layerstack;
mod snapshot;
mod trace;

use sandbox_protocol::{error_kind, Request, Response};
use sandbox_runtime::SandboxRuntimeOperations;

use super::DaemonObservability;

pub(crate) fn observability_view_response(
    operations: &SandboxRuntimeOperations,
    observability: Option<&DaemonObservability>,
    request: &Request,
) -> Response {
    let view = match request.optional_string("view") {
        Ok(view) => view,
        Err(response) => return response,
    };
    match view.as_deref() {
        Some("layerstack") => {
            layerstack::layerstack_view_response(operations, observability, request)
        }
        Some("snapshot") => snapshot::snapshot_view_response(operations, observability, request),
        Some("cgroup") => cgroup::cgroup_view_response(observability, request),
        Some("trace") => trace::trace_view_response(observability, request),
        Some("events") => events::events_view_response(observability, request),
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

/// Parse the bounded `window_ms` lookback shared by the `cgroup` and
/// `layerstack` views, rejecting values past the configured ceiling
/// (`observability.views.resource_window_ms`).
pub(super) fn resource_window_ms(
    request: &Request,
    max_window_ms: u64,
) -> Result<Option<u64>, Response> {
    let window_ms = request.optional_u64("window_ms")?;
    if let Some(window_ms) = window_ms {
        if window_ms > max_window_ms {
            return Err(Response::fault(
                error_kind::INVALID_REQUEST,
                format!("window_ms exceeds max ({max_window_ms})"),
            ));
        }
    }
    Ok(window_ms)
}

pub(super) fn observability_unconfigured() -> Response {
    Response::fault(
        error_kind::INTERNAL_ERROR,
        "daemon observability is not configured".to_owned(),
    )
}
