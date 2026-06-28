use sandbox_observability::sample_layerstack;
use sandbox_protocol::{Request, Response};
use sandbox_runtime::SandboxRuntimeOperations;
use serde_json::Value;

use crate::observability::layerstack::stack_summary_value;
use crate::observability::DaemonObservability;

/// The live `snapshot` view.
pub(super) fn snapshot_view_response(
    operations: &SandboxRuntimeOperations,
    observability: Option<&DaemonObservability>,
    _request: &Request,
) -> Response {
    let Some(observability) = observability else {
        return super::observability_unconfigured();
    };
    let mut snapshot = observability.snapshot_value(operations.observability_snapshot());
    if let (Ok(observation), Value::Object(object)) =
        (operations.observe_layerstack(), &mut snapshot)
    {
        let bytes = sample_layerstack(operations.layer_stack_root());
        object.insert(
            "stack".to_owned(),
            stack_summary_value(&observation, &bytes),
        );
    }
    Response::ok(snapshot)
}
