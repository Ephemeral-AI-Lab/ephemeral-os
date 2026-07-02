//! The live remount transaction: rewritten lease → quiesce → staged-switch
//! runner → report classification → swap/persist/resume → old-lease release.
//!
//! Pin-overlap holds throughout: the replacement lease is acquired before
//! anything can release the old one, and clean aborts release only the
//! replacement. The C5 rules are a pure function of the runner's two
//! booleans plus report presence, with a missing report classified by
//! comparing the workspace mount id against the quiesce-time read. On the
//! faulty path the frozen tasks are deliberately never resumed — nothing may
//! observe the partial mount state before the ordinary destroy.

use std::path::{Path, PathBuf};

use sandbox_runtime_layerstack::{manifest_root_hash, LayerStack, Lease, RewrittenLease};
use sandbox_runtime_namespace_execution::quiesce::{
    quiesce_holder_scope, workspace_mount_id, FrozenTasks, QuiesceOutcome, QuiesceSpec,
    DEFAULT_FREEZE_BUDGET,
};
use serde_json::Value;

use crate::model::{LeaseId, WorkspaceSessionId};
use crate::session::{WorkspaceManager, WorkspaceManagerError};

use super::leases::next_handle_id;

/// Process-wide live-remount gate verdict, set once at boot by
/// [`set_live_remount_gate`] and read by every remount attempt. `0` =
/// unprobed (disabled, fail-safe), `1` = proven, `2` = failed.
static LIVE_REMOUNT_GATE: std::sync::atomic::AtomicU8 = std::sync::atomic::AtomicU8::new(0);

/// Record the boot-time kernel-gate probe verdict. Until this is called with
/// `true`, live remount is disabled and every session reports
/// `leased(unsupported:kernel_gate_not_proven)`.
pub fn set_live_remount_gate(proven: bool) {
    LIVE_REMOUNT_GATE.store(
        if proven { 1 } else { 2 },
        std::sync::atomic::Ordering::Release,
    );
}

/// Probe the same-upperdir / userxattr kernel gate in `scratch_root` and set
/// the process-wide verdict. Returns whether live remount is enabled.
pub fn probe_and_set_live_remount_gate(scratch_root: &Path) -> bool {
    let proven = sandbox_runtime_namespace_process::gate::probe_live_remount_gate(scratch_root);
    set_live_remount_gate(proven);
    proven
}

#[must_use]
fn live_remount_enabled() -> bool {
    LIVE_REMOUNT_GATE.load(std::sync::atomic::Ordering::Acquire) == 1
}

/// One session's remount outcome, mapping 1:1 onto the C1 decision tree.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RemountOutcome {
    Identity,
    Migrated {
        released_old_lease: bool,
        release_error: Option<String>,
    },
    Leased {
        reason: String,
    },
    Faulty {
        class_detail: String,
    },
}

#[doc(hidden)]
#[derive(Debug, PartialEq, Eq)]
pub enum ReportClassification {
    CleanSkip { reason: String },
    Verified { parked_reason: Option<String> },
    Faulty { class_detail: String },
}

impl WorkspaceManager {
    pub(crate) fn remount_session(
        &mut self,
        layer_stack_root: &Path,
        workspace_id: &WorkspaceSessionId,
        cgroup_procs_path: Option<PathBuf>,
    ) -> Result<RemountOutcome, WorkspaceManagerError> {
        let Some(handle) = self.handles.get(workspace_id).cloned() else {
            return Err(WorkspaceManagerError::NotOpen);
        };
        let mut stack = LayerStack::open(layer_stack_root.to_path_buf())
            .map_err(|error| setup_failed("open layer stack", &error))?;
        let current = Lease {
            lease_id: handle.snapshot.lease_id.0.clone(),
            manifest: handle.snapshot.manifest.clone(),
            layer_paths: handle.snapshot.layer_paths.clone(),
        };
        let replacement = match stack.acquire_rewritten_lease(&current, &workspace_id.0) {
            Ok(RewrittenLease::Identity) => return Ok(RemountOutcome::Identity),
            Ok(RewrittenLease::Replaced(lease)) => lease,
            Err(error) => {
                return Ok(RemountOutcome::Leased {
                    reason: format!("mount_uncertain:lease_rewrite:{error}"),
                })
            }
        };
        if !live_remount_enabled() {
            release_replacement(&mut stack, &replacement.lease_id);
            return Ok(RemountOutcome::Leased {
                reason: "unsupported:kernel_gate_not_proven".to_owned(),
            });
        }

        let spec = QuiesceSpec {
            holder_pid: handle.holder_pid,
            workspace_root: PathBuf::from(&handle.workspace_root),
            cgroup_procs_path,
            runner_pids: Vec::new(),
            freeze_budget: DEFAULT_FREEZE_BUDGET,
        };
        let (frozen, pre_switch_mount_id): (Option<FrozenTasks>, u64) =
            match quiesce_holder_scope(&spec) {
                QuiesceOutcome::Blocked { reason } => {
                    release_replacement(&mut stack, &replacement.lease_id);
                    return Ok(RemountOutcome::Leased { reason });
                }
                QuiesceOutcome::NoObservableTasks { workspace_mount_id } => {
                    (None, workspace_mount_id)
                }
                QuiesceOutcome::Frozen {
                    tasks,
                    workspace_mount_id,
                } => (Some(tasks), workspace_mount_id),
            };

        let fresh_workdir = handle
            .dirs
            .run_dir
            .join(format!("work-remount-{}", next_handle_id()));
        let report =
            self.runtime
                .remount_overlay(&handle, replacement.layer_paths.clone(), &fresh_workdir);
        let payload = report.as_ref().ok().map(|result| &result.payload);
        let post_death_mount_id = if payload_has_report(payload) {
            None
        } else {
            Some(workspace_mount_id(
                handle.holder_pid,
                Path::new(&handle.workspace_root),
            ))
        };
        match classify_remount_report(payload, pre_switch_mount_id, post_death_mount_id) {
            ReportClassification::CleanSkip { reason } => {
                release_replacement(&mut stack, &replacement.lease_id);
                drop(frozen);
                Ok(RemountOutcome::Leased { reason })
            }
            ReportClassification::Verified { parked_reason } => {
                let old_lease_id = handle.snapshot.lease_id.0.clone();
                let parked = parked_reason.is_some();
                self.apply_switch(
                    workspace_id,
                    &replacement,
                    &fresh_workdir,
                    parked.then(|| old_lease_id.clone()),
                );
                let _ = self.persist_handles();
                drop(frozen);
                match parked_reason {
                    Some(reason) => Ok(RemountOutcome::Leased { reason }),
                    None => {
                        let release = stack.release_lease(&old_lease_id);
                        Ok(RemountOutcome::Migrated {
                            released_old_lease: matches!(release, Ok(true)),
                            release_error: release.err().map(|error| error.to_string()),
                        })
                    }
                }
            }
            ReportClassification::Faulty { class_detail } => {
                if let Some(session) = self.handles.get_mut(workspace_id) {
                    session.parked_lease_id = Some(replacement.lease_id.clone());
                }
                if let Some(tasks) = frozen {
                    std::mem::forget(tasks);
                }
                Ok(RemountOutcome::Faulty { class_detail })
            }
        }
    }

    fn apply_switch(
        &mut self,
        workspace_id: &WorkspaceSessionId,
        replacement: &Lease,
        fresh_workdir: &Path,
        parked_old_lease: Option<String>,
    ) {
        let Some(session) = self.handles.get_mut(workspace_id) else {
            return;
        };
        session.snapshot.lease_id = LeaseId(replacement.lease_id.clone());
        session.snapshot.manifest_version = replacement.manifest.version;
        session.snapshot.root_hash = manifest_root_hash(&replacement.manifest);
        session.snapshot.manifest = replacement.manifest.clone();
        session.snapshot.layer_paths = replacement.layer_paths.clone();
        session.dirs.workdir = fresh_workdir.to_path_buf();
        session.parked_lease_id = parked_old_lease;
    }
}

fn release_replacement(stack: &mut LayerStack, lease_id: &str) {
    let _ = stack.release_lease(lease_id);
}

fn payload_has_report(payload: Option<&Value>) -> bool {
    payload.is_some_and(|value| {
        value
            .get("first_move_succeeded")
            .and_then(Value::as_bool)
            .is_some()
            && value
                .get("mount_verified")
                .and_then(Value::as_bool)
                .is_some()
    })
}

/// The C5 table as a pure function: outcome = f(two booleans + report
/// presence), with a missing report decided by the workspace-mount-id
/// comparison (`post_death_mount_id` is `Some(current)` only when the report
/// is missing; `Some(None)` means the workspace row itself is gone).
#[doc(hidden)]
pub fn classify_remount_report(
    payload: Option<&Value>,
    pre_switch_mount_id: u64,
    post_death_mount_id: Option<Option<u64>>,
) -> ReportClassification {
    if payload_has_report(payload) {
        let payload = payload.unwrap_or(&Value::Null);
        let first_move = payload
            .get("first_move_succeeded")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let verified = payload
            .get("mount_verified")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let detail = payload
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or("mount_uncertain:report_without_detail")
            .to_owned();
        if !first_move {
            return ReportClassification::CleanSkip { reason: detail };
        }
        if !verified {
            return ReportClassification::Faulty {
                class_detail: detail,
            };
        }
        return match detail.as_str() {
            "switched" => ReportClassification::Verified {
                parked_reason: None,
            },
            "pinned:rollback_unmount_busy" => ReportClassification::Verified {
                parked_reason: Some(detail),
            },
            _ => ReportClassification::Faulty {
                class_detail: detail,
            },
        };
    }
    match post_death_mount_id {
        Some(Some(current)) if current == pre_switch_mount_id => ReportClassification::CleanSkip {
            reason: "stage_failed:runner_died_before_switch".to_owned(),
        },
        _ => ReportClassification::Faulty {
            class_detail: "mount_uncertain:runner_report_missing".to_owned(),
        },
    }
}

fn setup_failed(step: &str, error: &dyn std::fmt::Display) -> WorkspaceManagerError {
    WorkspaceManagerError::SetupFailed {
        step: format!("{step}: {error}"),
    }
}
