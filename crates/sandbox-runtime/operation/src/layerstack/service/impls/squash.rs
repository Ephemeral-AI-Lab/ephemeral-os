//! The daemon-local `squash_layerstack` operation: storage squash, the
//! per-session remount sweep, and result assembly.
//!
//! The storage commit is the correctness boundary; the sweep is best-effort
//! cleanup inside the same singleflight (the `SquashOutcome` flight guard
//! stays alive across it). `replaced_layers` derives from post-sweep disk
//! truth; `blocked_reasons` maps `Leased` sessions onto blocks by pre-attempt
//! manifest membership (never-straddle makes that whole-or-none); faulty
//! sessions are reported in the result line and destroyed through the
//! ordinary path.

use std::collections::BTreeSet;

use sandbox_observability::record::names;
use sandbox_runtime_layerstack::LayerStack;
use serde_json::{json, Value};

use crate::operation::OperationEntry;
use crate::services::SandboxRuntimeOperations;
use crate::workspace_session::{SweptDisposition, SweptSession};

const SQUASH_LAYERSTACK: OperationEntry = OperationEntry {
    name: "squash_layerstack",
    cli: None,
    dispatch: dispatch_squash_layerstack,
};

const OPERATIONS: &[OperationEntry] = &[SQUASH_LAYERSTACK];

pub(crate) const fn operation_entries() -> &'static [OperationEntry] {
    OPERATIONS
}

fn dispatch_squash_layerstack(
    operations: &SandboxRuntimeOperations,
    _request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    match run_squash_layerstack(operations) {
        Ok(value) => sandbox_protocol::Response::ok(value),
        Err(message) => {
            sandbox_protocol::Response::fault_with_details("operation_failed", message, json!({}))
        }
    }
}

fn run_squash_layerstack(operations: &SandboxRuntimeOperations) -> Result<Value, String> {
    operations
        .layerstack
        .obs
        .scope(names::LAYERSTACK_SQUASH, |span| {
            let root = operations.layerstack.layer_stack_root().to_path_buf();
            let mut stack = LayerStack::open(root.clone()).map_err(|error| error.to_string())?;
            let outcome = stack.squash().map_err(|error| error.to_string())?;
            span.attr("manifest_version", outcome.manifest.version);
            span.attr("blocks", outcome.blocks.len());

            let swept: Vec<SweptSession> = operations
                .workspace_session
                .session_ids()
                .iter()
                .map(|id| operations.workspace_session.remount_session(id))
                .collect();

            let mut faulty_sessions = Vec::new();
            for session in &swept {
                if let SweptDisposition::Faulty { class_detail } = &session.disposition {
                    let lease_errors = operations
                        .workspace_session
                        .destroy_faulty_session(&session.workspace_session_id);
                    faulty_sessions.push(json!({
                        "session_id": session.workspace_session_id.0,
                        "class_detail": class_detail,
                        "lease_errors": lease_errors,
                    }));
                }
            }

            let squashed_blocks: Vec<Value> = outcome
                .blocks
                .iter()
                .map(|block| {
                    let replaced_ids: BTreeSet<&str> = block
                        .replaced
                        .iter()
                        .map(|layer| layer.layer_id.as_str())
                        .collect();
                    let reclaimed = block
                        .replaced
                        .iter()
                        .all(|layer| !root.join(&layer.path).exists());
                    let mut entry = json!({
                        "squashed_layer_id": block.squashed_layer.layer_id,
                        "replaced_layer_ids": block
                            .replaced
                            .iter()
                            .map(|layer| layer.layer_id.clone())
                            .collect::<Vec<_>>(),
                        "replaced_layers": if reclaimed { "reclaimed" } else { "leased" },
                    });
                    if !reclaimed {
                        let mut reasons: Vec<String> = swept
                            .iter()
                            .filter_map(|session| match &session.disposition {
                                SweptDisposition::Leased { reason }
                                    if session
                                        .pre_manifest_layer_ids
                                        .iter()
                                        .any(|id| replaced_ids.contains(id.as_str())) =>
                                {
                                    Some(reason.clone())
                                }
                                _ => None,
                            })
                            .collect();
                        reasons.sort();
                        reasons.dedup();
                        if reasons.is_empty() {
                            reasons.push("pinned:lease_holder_not_swept".to_owned());
                        }
                        entry["blocked_reasons"] = json!(reasons);
                    }
                    entry
                })
                .collect();

            let mut result = json!({
                "manifest_version": outcome.manifest.version,
                "squashed_blocks": squashed_blocks,
            });
            if !faulty_sessions.is_empty() {
                result["faulty_sessions"] = json!(faulty_sessions);
            }
            Ok(result)
        })
}
