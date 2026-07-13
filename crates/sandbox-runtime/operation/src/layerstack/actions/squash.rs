//! Shared layerstack squash orchestration for manual and automatic callers.

use std::collections::BTreeSet;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, PoisonError, TryLockError};

use sandbox_observability_telemetry::record::names;
use sandbox_observability_telemetry::{sample_layerstack, TraceContext, WalkBudget};
use sandbox_runtime_layerstack::{
    manifest_root_hash, LayerStack, LayerStackError, SquashPhase, SquashPhaseObserver,
};
use serde_json::{json, Value};

use crate::layerstack::LayerStackService;
use crate::workspace_crate::WorkspaceSessionId;
use crate::workspace_session::{SweptDisposition, SweptSession, WorkspaceSessionService};

#[derive(Clone, Copy)]
pub(crate) enum SquashCause {
    Manual,
    Autosquash {
        policy: &'static str,
        threshold: usize,
        observed_layers: usize,
        trigger_reason: &'static str,
    },
}

pub(crate) struct SquashActionResult {
    pub(crate) manual_result: Value,
    pub(crate) before_layers: usize,
    pub(crate) after_layers: usize,
    pub(crate) blocks_committed: usize,
}

pub(crate) fn run_manual(
    layerstack: &Arc<LayerStackService>,
    workspace_session: &Arc<WorkspaceSessionService>,
) -> Result<SquashActionResult, String> {
    let _gate = match layerstack.squash_gate.try_lock() {
        Ok(guard) => guard,
        Err(TryLockError::Poisoned(error)) => error.into_inner(),
        Err(TryLockError::WouldBlock) => {
            return Err("layerstack squash already in flight".to_owned());
        }
    };
    execute(layerstack, workspace_session, SquashCause::Manual)
}

pub(crate) fn execute(
    layerstack: &Arc<LayerStackService>,
    workspace_session: &Arc<WorkspaceSessionService>,
    cause: SquashCause,
) -> Result<SquashActionResult, String> {
    layerstack.obs.scope(names::LAYERSTACK_SQUASH, |span| {
        match cause {
            SquashCause::Manual => {
                span.attr("cause", "manual");
            }
            SquashCause::Autosquash {
                policy,
                threshold,
                observed_layers,
                trigger_reason,
            } => {
                span.attr("cause", "autosquash")
                    .attr("policy", policy)
                    .attr("threshold", threshold)
                    .attr("observed_layers", observed_layers)
                    .attr("trigger_reason", trigger_reason);
            }
        }

        let root = layerstack.layer_stack_root().to_path_buf();
        let mut stack = LayerStack::open(root.clone()).map_err(|error| error.to_string())?;
        let phase_observer = TelemetrySquashPhaseObserver {
            observer: &layerstack.obs,
        };
        let outcome = stack
            .squash_with_observer(&phase_observer)
            .map_err(|error| error.to_string())?;
        let blocks_committed = outcome.blocks.len();
        let after_layers = outcome.manifest.layers.len();
        let before_layers = after_layers - blocks_committed
            + outcome
                .blocks
                .iter()
                .map(|block| block.replaced.len())
                .sum::<usize>();
        span.attr("manifest_version", outcome.manifest.version)
            .attr("s2_root_hash", manifest_root_hash(&outcome.manifest))
            .attr("blocks", blocks_committed);
        attach_post_commit_snapshot(span, &root);

        let ids = workspace_session.session_ids();
        span.attr("swept", ids.len())
            .attr("sweep_width", layerstack.config.remount_sweep_width);
        let swept = layerstack
            .obs
            .scope(names::LAYERSTACK_SQUASH_REMOUNT_SWEEP, |sweep_span| {
                sweep_span
                    .attr("sessions", ids.len())
                    .attr("width", layerstack.config.remount_sweep_width);
                Ok::<_, std::convert::Infallible>(remount_sweep(
                    layerstack,
                    workspace_session,
                    &ids,
                    layerstack.obs.context(),
                ))
            })
            .unwrap_or_else(|never| match never {});
        if swept
            .iter()
            .any(|session| session.disposition == SweptDisposition::Migrated)
        {
            let _ = workspace_session.persist_handles();
        }

        let mut faulty_sessions = Vec::new();
        for session in &swept {
            if let SweptDisposition::Faulty { class_detail } = &session.disposition {
                let lease_errors =
                    workspace_session.destroy_faulty_session(&session.workspace_session_id);
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

        let swept_sessions: Vec<Value> = swept
            .iter()
            .map(|session| {
                let mut entry = json!({
                    "session_id": session.workspace_session_id.0,
                    "disposition": disposition_name(&session.disposition),
                });
                match &session.disposition {
                    SweptDisposition::Leased { reason } => entry["reason"] = json!(reason),
                    SweptDisposition::Faulty { class_detail } => {
                        entry["class_detail"] = json!(class_detail);
                    }
                    SweptDisposition::SessionGone
                    | SweptDisposition::Identity
                    | SweptDisposition::Migrated => {}
                }
                entry
            })
            .collect();

        let mut manual_result = json!({
            "manifest_version": outcome.manifest.version,
            "squashed_blocks": squashed_blocks,
            "swept_sessions": swept_sessions,
        });
        if !faulty_sessions.is_empty() {
            manual_result["faulty_sessions"] = json!(faulty_sessions);
        }
        Ok(SquashActionResult {
            manual_result,
            before_layers,
            after_layers,
            blocks_committed,
        })
    })
}

fn attach_post_commit_snapshot(
    span: &sandbox_observability_telemetry::SpanGuard,
    storage_root: &std::path::Path,
) {
    let snapshot = sample_layerstack(storage_root, WalkBudget::default());
    span.attr("s2_layer_count", snapshot.layers.len());
    if let Some(value) = snapshot.total_bytes {
        span.attr("s2_active_logical_bytes", value);
    }
    if let Some(value) = snapshot.total_allocated_bytes {
        span.attr("s2_active_allocated_bytes", value);
    }
    if let Some(value) = snapshot.storage_logical_bytes {
        span.attr("s2_storage_logical_bytes", value);
    }
    if let Some(value) = snapshot.storage_allocated_bytes {
        span.attr("s2_storage_allocated_bytes", value);
    }
    if let Some(value) = snapshot.staging_entry_count {
        span.attr("s2_staging_entry_count", value);
    }
}

struct TelemetrySquashPhaseObserver<'a> {
    observer: &'a sandbox_observability_telemetry::Observer,
}

impl SquashPhaseObserver for TelemetrySquashPhaseObserver<'_> {
    fn observe<T>(
        &self,
        phase: SquashPhase,
        body: impl FnOnce() -> Result<T, LayerStackError>,
    ) -> Result<T, LayerStackError> {
        let name = match phase {
            SquashPhase::Plan => names::LAYERSTACK_SQUASH_PLAN,
            SquashPhase::Flatten => names::LAYERSTACK_SQUASH_FLATTEN,
            SquashPhase::Commit => names::LAYERSTACK_SQUASH_COMMIT,
        };
        self.observer.scope(name, |_| body())
    }
}

fn disposition_name(disposition: &SweptDisposition) -> &'static str {
    match disposition {
        SweptDisposition::SessionGone => "session_gone",
        SweptDisposition::Identity => "identity",
        SweptDisposition::Migrated => "migrated",
        SweptDisposition::Leased { .. } => "leased",
        SweptDisposition::Faulty { .. } => "faulty",
    }
}

fn remount_sweep(
    layerstack: &LayerStackService,
    workspace_session: &WorkspaceSessionService,
    ids: &[WorkspaceSessionId],
    ctx: Option<TraceContext>,
) -> Vec<SweptSession> {
    if ids.is_empty() {
        return Vec::new();
    }
    let width = layerstack.config.remount_sweep_width.min(ids.len());
    if width <= 1 {
        return ids
            .iter()
            .map(|id| workspace_session.remount_session(id))
            .collect();
    }
    let cursor = AtomicUsize::new(0);
    let slots: Vec<Mutex<Option<SweptSession>>> =
        (0..ids.len()).map(|_| Mutex::new(None)).collect();
    let obs = &layerstack.obs;
    std::thread::scope(|scope| {
        for _ in 0..width {
            scope.spawn(|| loop {
                let index = cursor.fetch_add(1, Ordering::Relaxed);
                let Some(id) = ids.get(index) else {
                    break;
                };
                let swept = obs.with_context(ctx.clone(), || workspace_session.remount_session(id));
                *slots[index].lock().unwrap_or_else(PoisonError::into_inner) = Some(swept);
            });
        }
    });
    slots
        .into_iter()
        .filter_map(|slot| slot.into_inner().unwrap_or_else(PoisonError::into_inner))
        .collect()
}
