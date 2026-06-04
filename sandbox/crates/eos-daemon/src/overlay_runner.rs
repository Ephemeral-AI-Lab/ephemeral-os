//! Shared overlay ns-runner helpers and daemon adapters.

use std::io::Write;
#[cfg(target_os = "linux")]
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::path::PathBuf;
use std::process::{Command, Stdio};

use eos_ephemeral_workspace::{
    EphemeralDirAllocator, EphemeralRunDirs, EphemeralSnapshot, EphemeralSnapshotPort,
    EphemeralWorkspaceError, FreshNamespaceRunnerPort, InvocationId, PathChange, PublishOutcome,
    PublishStatus, WorkspacePublisherPort, WorkspaceRoot,
};
use eos_layerstack::{LayerStack, Manifest};
use eos_occ::{ChangesetResult, FileResult, OccStatus};
use eos_overlay::overlay_writable_root;
use eos_protocol::{LayerChange, LayerPath};
use eos_runner::{RunRequest, RunResult};
use serde_json::{json, Value};

use crate::error::DaemonError;
use crate::invocation_registry::InFlightRegistry;
use crate::occ_writer::{
    apply_occ_changeset, base_hashes_for_snapshot, insert_occ_route_timings, manifest_version_u64,
    occ_route_metrics,
};

pub(crate) use eos_ephemeral_workspace::RunDirCleanup;

pub(crate) struct DaemonSnapshotPort;

impl EphemeralSnapshotPort for DaemonSnapshotPort {
    fn acquire_snapshot(
        &self,
        root: &WorkspaceRoot,
        request_id: &str,
    ) -> Result<EphemeralSnapshot, EphemeralWorkspaceError> {
        let mut stack = LayerStack::open(root.0.clone()).map_err(|error| {
            EphemeralWorkspaceError::SnapshotAcquire {
                reason: error.to_string(),
            }
        })?;
        let lease = stack.acquire_snapshot(request_id).map_err(|error| {
            EphemeralWorkspaceError::SnapshotAcquire {
                reason: error.to_string(),
            }
        })?;
        Ok(EphemeralSnapshot {
            lease_id: lease.lease_id,
            manifest_version: lease.manifest_version,
            manifest_root_hash: lease.root_hash,
            layer_paths: lease.layer_paths.into_iter().map(PathBuf::from).collect(),
        })
    }

    fn release_lease(
        &self,
        root: &WorkspaceRoot,
        lease_id: &str,
    ) -> Result<bool, EphemeralWorkspaceError> {
        let mut stack = LayerStack::open(root.0.clone()).map_err(|error| {
            EphemeralWorkspaceError::LeaseRelease {
                lease_id: lease_id.to_owned(),
                reason: error.to_string(),
            }
        })?;
        stack
            .release_lease(lease_id)
            .map_err(|error| EphemeralWorkspaceError::LeaseRelease {
                lease_id: lease_id.to_owned(),
                reason: error.to_string(),
            })
    }
}

pub(crate) struct DaemonFreshNamespaceRunner<'a> {
    invocation_registry: Option<&'a InFlightRegistry>,
}

impl<'a> DaemonFreshNamespaceRunner<'a> {
    pub(crate) const fn new(invocation_registry: Option<&'a InFlightRegistry>) -> Self {
        Self {
            invocation_registry,
        }
    }
}

impl FreshNamespaceRunnerPort for DaemonFreshNamespaceRunner<'_> {
    fn run(&self, request: &RunRequest) -> Result<RunResult, EphemeralWorkspaceError> {
        run_ns_runner_child(request, self.invocation_registry).map_err(|error| {
            EphemeralWorkspaceError::RunnerFailed {
                reason: error.to_string(),
            }
        })
    }
}

pub(crate) struct DaemonPublisherPort<'a> {
    root: &'a Path,
    manifest: &'a Manifest,
}

impl<'a> DaemonPublisherPort<'a> {
    pub(crate) const fn new(root: &'a Path, manifest: &'a Manifest) -> Self {
        Self { root, manifest }
    }
}

impl WorkspacePublisherPort for DaemonPublisherPort<'_> {
    fn publish_upperdir_changes(
        &self,
        _root: &WorkspaceRoot,
        snapshot: &EphemeralSnapshot,
        changes: &[LayerChange],
        _path_kinds: &[PathChange],
    ) -> Result<PublishOutcome, EphemeralWorkspaceError> {
        let route_start = std::time::Instant::now();
        let route_metrics = occ_route_metrics(self.root, changes).map_err(|error| {
            EphemeralWorkspaceError::PublishFailed {
                reason: error.to_string(),
            }
        })?;
        let route_s = route_start.elapsed().as_secs_f64();
        let base_hashes =
            base_hashes_for_snapshot(self.root, self.manifest, changes).map_err(|error| {
                EphemeralWorkspaceError::PublishFailed {
                    reason: error.to_string(),
                }
            })?;
        let occ_start = std::time::Instant::now();
        let mut changeset = apply_occ_changeset(
            self.root,
            Some(
                manifest_version_u64(snapshot.manifest_version).map_err(|error| {
                    EphemeralWorkspaceError::PublishFailed {
                        reason: error.to_string(),
                    }
                })?,
            ),
            changes,
            &base_hashes,
        )
        .map_err(|error| EphemeralWorkspaceError::PublishFailed {
            reason: error.to_string(),
        })?;
        let occ_s = occ_start.elapsed().as_secs_f64();
        let mut timing_values = serde_json::Map::new();
        insert_occ_route_timings(&mut timing_values, route_metrics, route_s, occ_s);
        for (key, value) in timing_values {
            if let Some(value) = value.as_f64() {
                changeset.timings.entry(key).or_insert(value);
            }
        }
        Ok(publish_outcome_from_changeset(&changeset))
    }
}

pub(crate) fn ephemeral_dir_allocator() -> Result<EphemeralDirAllocator, DaemonError> {
    Ok(EphemeralDirAllocator::new(
        overlay_writable_root()
            .map_err(|err| overlay_daemon_error("overlay writable root", &err))?
            .join("runtime"),
    ))
}

pub(crate) fn overlay_run_dirs(
    kind: &str,
    invocation_id: &str,
) -> Result<EphemeralRunDirs, DaemonError> {
    ephemeral_dir_allocator()?
        .allocate(kind, &InvocationId(invocation_id.to_owned()))
        .map_err(ephemeral_daemon_error)
}

pub(crate) fn run_ns_runner_child(
    request: &RunRequest,
    invocation_registry: Option<&InFlightRegistry>,
) -> Result<RunResult, DaemonError> {
    let payload =
        serde_json::to_vec(request).map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?;
    let mut command = Command::new(std::env::current_exe()?);
    command
        .arg("ns-runner")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(target_os = "linux")]
    command.process_group(0);
    let mut child = command.spawn()?;
    if let Some(registry) = invocation_registry {
        if let Ok(pgid) = i32::try_from(child.id()) {
            registry.register_process_group(&request.tool_call.invocation_id, pgid);
        }
    }
    child
        .stdin
        .as_mut()
        .ok_or_else(|| DaemonError::OverlayPipeline("ns-runner stdin unavailable".to_owned()))?
        .write_all(&payload)?;
    let output = child.wait_with_output()?;
    if let Some(registry) = invocation_registry {
        registry.clear_process_group(&request.tool_call.invocation_id);
    }
    if !output.status.success() {
        return Err(DaemonError::OverlayPipeline(format!(
            "ns-runner exited with status {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        )));
    }
    serde_json::from_slice::<RunResult>(&output.stdout)
        .map_err(|err| DaemonError::OverlayPipeline(format!("invalid ns-runner output: {err}")))
}

pub(crate) fn overlay_daemon_error(context: &str, err: &eos_overlay::OverlayError) -> DaemonError {
    DaemonError::OverlayPipeline(format!("{context}: {err}"))
}

pub(crate) fn ephemeral_daemon_error(error: EphemeralWorkspaceError) -> DaemonError {
    match error {
        EphemeralWorkspaceError::InvalidArgument(message) => DaemonError::InvalidEnvelope(message),
        EphemeralWorkspaceError::Io { source, .. } => DaemonError::Io(source),
        other => DaemonError::OverlayPipeline(other.to_string()),
    }
}

pub(crate) fn path_changes_to_wire(path_changes: &[PathChange]) -> Vec<(String, String)> {
    path_changes
        .iter()
        .map(|change| {
            (
                change.path.clone(),
                path_change_kind_wire(change.kind).to_owned(),
            )
        })
        .collect()
}

pub(crate) fn path_change_kind_wire(kind: eos_ephemeral_workspace::PathChangeKind) -> &'static str {
    match kind {
        eos_ephemeral_workspace::PathChangeKind::Write => "write",
        eos_ephemeral_workspace::PathChangeKind::Delete => "delete",
        eos_ephemeral_workspace::PathChangeKind::Symlink => "symlink",
        eos_ephemeral_workspace::PathChangeKind::OpaqueDir => "opaque_dir",
    }
}

pub(crate) fn changeset_from_publish_outcome(
    outcome: &PublishOutcome,
) -> Result<ChangesetResult, DaemonError> {
    let raw = outcome
        .raw
        .as_object()
        .ok_or_else(|| DaemonError::OverlayPipeline("publish outcome raw must be object".into()))?;
    let files = raw
        .get("files")
        .and_then(Value::as_array)
        .ok_or_else(|| DaemonError::OverlayPipeline("publish outcome missing files".into()))?
        .iter()
        .map(file_result_from_value)
        .collect::<Result<Vec<_>, _>>()?;
    let timings = raw
        .get("timings")
        .and_then(Value::as_object)
        .map(|timings| {
            timings
                .iter()
                .filter_map(|(key, value)| value.as_f64().map(|value| (key.clone(), value)))
                .collect()
        })
        .unwrap_or_default();
    Ok(ChangesetResult {
        files,
        published_manifest_version: raw
            .get("published_manifest_version")
            .and_then(Value::as_u64),
        timings,
    })
}

fn publish_outcome_from_changeset(result: &ChangesetResult) -> PublishOutcome {
    let published_paths = result
        .files
        .iter()
        .filter(|file| file.status.is_published())
        .map(|file| file.path.as_str().to_owned())
        .collect::<Vec<_>>();
    let conflicts = result
        .files
        .iter()
        .filter(|file| !file.status.is_success())
        .map(|file| file.path.as_str().to_owned())
        .collect::<Vec<_>>();
    let status = if !conflicts.is_empty() {
        if result.files.iter().any(|file| {
            matches!(
                file.status,
                OccStatus::AbortedVersion | OccStatus::AbortedOverlap
            )
        }) {
            PublishStatus::Conflict
        } else {
            PublishStatus::Rejected
        }
    } else if published_paths.is_empty() {
        PublishStatus::NoChanges
    } else {
        PublishStatus::Published
    };
    PublishOutcome {
        status,
        manifest_version: result.published_manifest_version,
        published_paths,
        conflicts,
        timings: result
            .timings
            .iter()
            .map(|(key, value)| (key.clone(), json!(value)))
            .collect(),
        raw: json!({
            "files": result.files.iter().map(file_result_to_value).collect::<Vec<_>>(),
            "published_manifest_version": result.published_manifest_version,
            "timings": result.timings,
        }),
    }
}

fn file_result_to_value(file: &FileResult) -> Value {
    json!({
        "path": file.path.as_str(),
        "status": file.status,
        "message": file.message,
    })
}

fn file_result_from_value(value: &Value) -> Result<FileResult, DaemonError> {
    let object = value
        .as_object()
        .ok_or_else(|| DaemonError::OverlayPipeline("publish file result must be object".into()))?;
    let path = object
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| DaemonError::OverlayPipeline("publish file result missing path".into()))?;
    let status_value = object
        .get("status")
        .cloned()
        .ok_or_else(|| DaemonError::OverlayPipeline("publish file result missing status".into()))?;
    let status = serde_json::from_value::<OccStatus>(status_value)
        .map_err(|error| DaemonError::InvalidEnvelope(error.to_string()))?;
    Ok(FileResult {
        path: LayerPath::parse(path).map_err(eos_layerstack::LayerStackError::from)?,
        status,
        message: object
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned(),
    })
}
