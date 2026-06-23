use crate::layerstack::{LayerStackService, LayerStackServiceError, SquashLayerStackResult};
use crate::operation::{CliOperationSpec, CliSpec};
use crate::SandboxRuntimeOperations;
use sandbox_protocol::{Request, Response};
use serde_json::{json, Value};

use super::publish_changes::{layer_paths, revision_from_manifest};

pub(crate) const SPEC: CliOperationSpec = CliOperationSpec {
    name: "squash",
    family: "layerstack",
    summary: "Squash committed layer stack revisions.",
    description: "Compact the runtime layer stack into a single current revision when squashable layers exist.",
    args: &[],
    cli: Some(CliSpec {
        path: &["runtime", "squash"],
        usage: "sandbox-cli runtime squash",
        examples: &["sandbox-cli runtime squash"],
    }),
    related: &[],
};

pub(crate) fn dispatch(operations: &SandboxRuntimeOperations, _request: &Request) -> Response {
    squash_response(operations.layerstack.squash())
}

impl LayerStackService {
    pub fn squash(&self) -> Result<SquashLayerStackResult, LayerStackServiceError> {
        let mut stack = sandbox_runtime_layerstack::LayerStack::open(self.layer_stack_root.clone())
            .map_err(|error| LayerStackServiceError::LayerStack {
                operation: "open",
                error,
            })?;
        let outcome = stack
            .squash()
            .map_err(|error| LayerStackServiceError::LayerStack {
                operation: "squash",
                error,
            })?;
        let Some(manifest) = outcome.manifest else {
            return Ok(SquashLayerStackResult {
                squashed: false,
                revision: None,
                layer_paths: Vec::new(),
                lease_release_error: outcome.lease_release_error.map(|err| err.to_string()),
            });
        };
        Ok(SquashLayerStackResult {
            squashed: true,
            revision: Some(revision_from_manifest(&manifest)),
            layer_paths: layer_paths(&self.layer_stack_root, &manifest),
            lease_release_error: outcome.lease_release_error.map(|err| err.to_string()),
        })
    }
}

fn squash_response(result: Result<SquashLayerStackResult, LayerStackServiceError>) -> Response {
    match result {
        Ok(result) => Response::ok(squash_result_value(result)),
        Err(error) => Response::fault_with_details(
            "operation_failed",
            error.to_string(),
            json!({ "kind": error.kind() }),
        ),
    }
}

fn squash_result_value(result: SquashLayerStackResult) -> Value {
    json!({
        "squashed": result.squashed,
        "revision": revision_value(result.revision),
        "layer_paths": result
            .layer_paths
            .into_iter()
            .map(|path| path.to_string_lossy().into_owned())
            .collect::<Vec<_>>(),
        "lease_release_error": result.lease_release_error,
    })
}

fn revision_value(revision: Option<crate::layerstack::LayerStackRevision>) -> Value {
    revision.map_or(Value::Null, |revision| {
        json!({
            "manifest_version": revision.manifest_version,
            "root_hash": revision.root_hash,
            "layer_count": revision.layer_count,
        })
    })
}
