use std::collections::BTreeMap;
use std::path::Path;
use std::time::Instant;

use layerstack::service::{self, BoundedCaptureOptions, Snapshot};
use layerstack::{CaptureRouteStats, ChangesetResult, CommitOptions, CommitStatus, FileResult};
use serde_json::{json, Map, Value};
use trace::usize_to_f64_saturating;
use workspace::overlay::capture::{
    capture_upperdir, capture_upperdir_for_snapshot_with_options, RoutedCapturedChanges,
};
use workspace::overlay::tree::TreeResourceStats;
use workspace::profile::WorkspaceModeContext;

use super::command_workspace::OneShotCommandWorkspace;
use super::contract::{
    u64_to_f64_saturating, CommandMetadata, CommandResponse, IgnoredPublishLaneMetadata,
    PublishLanesMetadata, SourcePublishLaneMetadata, PUBLISH_LANES_METADATA_KEY,
    PUBLISH_REJECTION_DETAILS_METADATA_KEY,
};
use super::outcome::{
    ChangedPathKinds, FinalizeCommandRequest, MutationSource, WorkspaceApiError, WorkspaceConflict,
    WorkspaceKind, WorkspaceTimings,
};
use crate::core::changed_path_kind_pairs;
use crate::{CommandId, MutationCore};

pub(crate) fn finalize_one_shot_command_with_capture_options(
    root: &Path,
    snapshot: &Snapshot,
    workspace: &OneShotCommandWorkspace,
    commit_options: CommitOptions,
    mut capture_options: BoundedCaptureOptions,
    request: FinalizeCommandRequest,
) -> Result<CommandResponse, WorkspaceApiError> {
    let mut timings = base_timings(root)?;
    let command_success = request.command_succeeded();
    capture_options.materialize_payloads = command_success;
    let spool_dir = workspace
        .dirs()
        .run_dir
        .join("spool")
        .join("publish-capture");
    let captured = match capture_upperdir_for_snapshot_with_options(
        root,
        snapshot,
        &workspace.dirs().upperdir,
        &spool_dir,
        capture_options,
    ) {
        Ok(captured) => captured,
        Err(error) if !command_success => {
            timings.insert(
                "command_exec.capture_upperdir_error".to_owned(),
                json!(error.to_string()),
            );
            copy_runner_timings(&mut timings, request.runner_result.as_ref());
            insert_command_timings(&mut timings, 0, 0.0, 0.0, request.command_elapsed_s, false);
            return Ok(command_response(
                WorkspaceKind::Host,
                request,
                CommandFinalization {
                    success: false,
                    changed_paths: Vec::new(),
                    changed_path_kinds: ChangedPathKinds::default(),
                    mutation_source: None,
                    conflict: None,
                    conflict_reason: None,
                    timings,
                    extras: publish_lanes_extras(PublishLanesMetadata::dropped_command_failed(
                        snapshot.manifest_version,
                    )),
                },
            ));
        }
        Err(error) => {
            let error = error.to_string();
            timings.insert(
                "command_exec.capture_upperdir_error".to_owned(),
                json!(error.clone()),
            );
            copy_runner_timings(&mut timings, request.runner_result.as_ref());
            insert_command_timings(&mut timings, 0, 0.0, 0.0, request.command_elapsed_s, false);
            return Ok(command_response(
                WorkspaceKind::Host,
                request,
                CommandFinalization {
                    success: false,
                    changed_paths: Vec::new(),
                    changed_path_kinds: ChangedPathKinds::default(),
                    mutation_source: None,
                    conflict: None,
                    conflict_reason: Some(error),
                    timings,
                    extras: publish_lanes_extras(command_finalize_failed_lanes(
                        snapshot.manifest_version,
                    )),
                },
            ));
        }
    };
    let RoutedCapturedChanges {
        captured: captured_changes,
        route_stats,
        metadata_path_count: captured_path_count,
        spool_dir,
    } = captured;
    let _spool_cleanup = SpoolCleanup::new(spool_dir);
    let changed_path_kinds: ChangedPathKinds =
        changed_path_kind_pairs(&captured_changes.changes).collect();

    insert_tree_resource_timings(
        &mut timings,
        "resource.command_exec.upperdir",
        &captured_changes.stats,
    );
    timings.insert(
        "resource.command_exec.upperdir_tree_sampler_duration_us".to_owned(),
        json!(captured_changes.capture_s * 1_000_000.0),
    );
    let run_dir_walk_start = Instant::now();
    let run_dir_stats = TreeResourceStats::collect(&workspace.dirs().run_dir);
    insert_tree_resource_timings(
        &mut timings,
        "resource.command_exec.run_dir",
        &run_dir_stats,
    );
    timings.insert(
        "resource.command_exec.run_dir_tree_sampler_duration_us".to_owned(),
        json!(u64::try_from(run_dir_walk_start.elapsed().as_micros()).unwrap_or(u64::MAX)),
    );
    copy_runner_timings(&mut timings, request.runner_result.as_ref());

    if !command_success {
        let publish_lanes = publish_lanes_with_route_drop_summary(
            dropped_command_failed_lanes_with_counts(
                route_stats.gated_path_count,
                route_stats.direct_path_count,
                route_stats.direct_bytes,
                snapshot.manifest_version,
            ),
            route_stats.drop_path_count,
            route_stats.drop_reason_counts.clone(),
        );
        insert_command_timings(
            &mut timings,
            captured_path_count,
            captured_changes.capture_s,
            0.0,
            request.command_elapsed_s,
            false,
        );
        return Ok(command_response(
            WorkspaceKind::Host,
            request,
            CommandFinalization {
                success: false,
                changed_paths: Vec::new(),
                changed_path_kinds: ChangedPathKinds::default(),
                mutation_source: None,
                conflict: None,
                conflict_reason: None,
                timings,
                extras: publish_lanes_extras(publish_lanes),
            },
        ));
    }

    let publish_start = Instant::now();
    let changeset = match service::publish_command_capture_lane_aware(
        root,
        snapshot.manifest_version,
        &snapshot.layer_paths,
        &captured_changes.changes,
        &captured_changes.protected_drops,
        commit_options,
    ) {
        Ok(changeset) => changeset,
        Err(error) => {
            let error = error.to_string();
            let publish_s = publish_start.elapsed().as_secs_f64();
            timings.insert(
                "command_exec.publish_error".to_owned(),
                json!(error.clone()),
            );
            insert_command_timings(
                &mut timings,
                captured_path_count,
                captured_changes.capture_s,
                publish_s,
                request.command_elapsed_s,
                false,
            );
            return Ok(command_response(
                WorkspaceKind::Host,
                request,
                CommandFinalization {
                    success: false,
                    changed_paths: Vec::new(),
                    changed_path_kinds,
                    mutation_source: Some(MutationSource::OverlayCapture),
                    conflict: None,
                    conflict_reason: Some(error),
                    timings,
                    extras: publish_lanes_extras(publish_lanes_from_publish_error(
                        route_stats,
                        snapshot.manifest_version,
                    )),
                },
            ));
        }
    };
    let publish_s = publish_start.elapsed().as_secs_f64();

    let first_conflict = changeset.first_conflict();
    let publish_success = changeset.success();
    let publish_lanes =
        publish_lanes_from_changeset(&changeset, route_stats, snapshot.manifest_version);
    let publish_rejection_details =
        publish_rejection_details_from_changeset(&changeset, &publish_lanes);

    for (key, value) in &changeset.timings {
        timings.insert(key.clone(), json!(value));
    }
    let occ_s = changeset
        .timings
        .get("occ.commit.total_s")
        .copied()
        .unwrap_or(publish_s);
    insert_command_timings(
        &mut timings,
        captured_path_count,
        captured_changes.capture_s,
        occ_s,
        request.command_elapsed_s,
        false,
    );

    Ok(command_response(
        WorkspaceKind::Host,
        request,
        CommandFinalization {
            success: command_success && publish_success,
            changed_paths: changeset.published_paths(),
            changed_path_kinds,
            mutation_source: Some(MutationSource::OverlayCapture),
            conflict: first_conflict.map(conflict_from_file),
            conflict_reason: first_conflict.map(conflict_message).map(str::to_owned),
            timings,
            extras: publish_lanes_extras_with_rejections(publish_lanes, publish_rejection_details),
        },
    ))
}

pub(crate) fn finalize_workspace_command(
    context: &WorkspaceModeContext,
    request: FinalizeCommandRequest,
) -> Result<CommandResponse, WorkspaceApiError> {
    let mut timings = base_timings(&context.layer_stack_root)?;
    let captured = capture_upperdir(&context.upperdir)
        .map_err(|err| finalize_error(format!("capture isolated upperdir: {err}")))?;
    let changed_path_kinds: ChangedPathKinds = changed_path_kind_pairs(&captured.changes).collect();
    let changed_paths: Vec<String> = changed_path_kinds.keys().cloned().collect();
    insert_tree_resource_timings(
        &mut timings,
        "resource.command_exec.upperdir",
        &captured.stats,
    );
    timings.insert(
        "resource.command_exec.upperdir_tree_sampler_duration_us".to_owned(),
        json!(captured.capture_s * 1_000_000.0),
    );
    copy_runner_timings(&mut timings, request.runner_result.as_ref());
    let command_success = request.command_succeeded();
    insert_command_timings(
        &mut timings,
        changed_paths.len(),
        captured.capture_s,
        0.0,
        request.command_elapsed_s,
        true,
    );
    let mut extras = Map::new();
    extras.insert(
        "isolated_network".to_owned(),
        json!({
            "caller_id": context.caller_id,
            "workspace_handle_id": context.workspace_handle_id,
            "manifest_version": context.manifest_version,
            "manifest_root_hash": context.manifest_root_hash,
            "published": false,
        }),
    );
    extras.insert("warnings".to_owned(), json!([]));
    PublishLanesMetadata::empty(context.manifest_version).insert_into(&mut extras);
    let mut response = command_response(
        WorkspaceKind::IsolatedNetwork,
        request,
        CommandFinalization {
            success: command_success,
            changed_paths,
            changed_path_kinds,
            mutation_source: Some(MutationSource::IsolatedNetwork),
            conflict: None,
            conflict_reason: None,
            timings,
            extras,
        },
    );
    response.exit_code = Some(response.exit_code.unwrap_or(1));
    Ok(response)
}

pub(crate) fn discarded_response(
    workspace_kind: WorkspaceKind,
    request: FinalizeCommandRequest,
    route_manifest_version: Option<i64>,
) -> CommandResponse {
    let extras = publish_lanes_extras(PublishLanesMetadata::dropped_command_failed(
        route_manifest_version.unwrap_or(0),
    ));
    command_response(
        workspace_kind,
        request,
        CommandFinalization {
            success: false,
            changed_paths: Vec::new(),
            changed_path_kinds: ChangedPathKinds::default(),
            mutation_source: None,
            conflict: None,
            conflict_reason: None,
            timings: WorkspaceTimings::default(),
            extras,
        },
    )
}

pub(crate) fn finalization_error_response(
    workspace_kind: WorkspaceKind,
    request: FinalizeCommandRequest,
    route_manifest_version: Option<i64>,
    error: impl std::fmt::Display,
) -> CommandResponse {
    let error = error.to_string();
    let mut timings = WorkspaceTimings::default();
    timings.insert(
        "command_exec.finalize_error".to_owned(),
        json!(error.clone()),
    );
    command_response(
        workspace_kind,
        request,
        CommandFinalization {
            success: false,
            changed_paths: Vec::new(),
            changed_path_kinds: ChangedPathKinds::default(),
            mutation_source: None,
            conflict: None,
            conflict_reason: Some(error),
            timings,
            extras: publish_lanes_extras(command_finalize_failed_lanes(
                route_manifest_version.unwrap_or(0),
            )),
        },
    )
}

struct CommandFinalization {
    success: bool,
    changed_paths: Vec<String>,
    changed_path_kinds: ChangedPathKinds,
    mutation_source: Option<MutationSource>,
    conflict: Option<WorkspaceConflict>,
    conflict_reason: Option<String>,
    timings: WorkspaceTimings,
    extras: Map<String, Value>,
}

struct SpoolCleanup {
    path: Option<std::path::PathBuf>,
}

impl SpoolCleanup {
    fn new(path: Option<std::path::PathBuf>) -> Self {
        Self { path }
    }
}

impl Drop for SpoolCleanup {
    fn drop(&mut self) {
        if let Some(path) = &self.path {
            let _ = std::fs::remove_dir_all(path);
        }
    }
}

fn command_response(
    workspace_kind: WorkspaceKind,
    request: FinalizeCommandRequest,
    finalization: CommandFinalization,
) -> CommandResponse {
    CommandResponse {
        status: request.status,
        exit_code: request.exit_code,
        stdout: request.stdout,
        stderr: request.stderr,
        command_id: request.command_id.map(CommandId::new),
        finalized: Some(CommandMetadata {
            core: MutationCore {
                success: finalization.success,
                changed_paths: finalization.changed_paths,
                changed_path_kinds: finalization.changed_path_kinds,
                mutation_source: finalization.mutation_source,
                conflict: finalization.conflict,
                conflict_reason: finalization.conflict_reason,
                timings: finalization.timings,
            },
            workspace: workspace_kind,
            extras: finalization.extras,
        }),
    }
}

fn publish_lanes_extras(publish_lanes: PublishLanesMetadata) -> Map<String, Value> {
    publish_lanes_extras_with_rejections(publish_lanes, Vec::new())
}

fn publish_lanes_extras_with_rejections(
    publish_lanes: PublishLanesMetadata,
    rejection_details: Vec<Value>,
) -> Map<String, Value> {
    let mut extras = Map::new();
    extras.insert(
        PUBLISH_LANES_METADATA_KEY.to_owned(),
        publish_lanes.to_value(),
    );
    if !rejection_details.is_empty() {
        extras.insert(
            PUBLISH_REJECTION_DETAILS_METADATA_KEY.to_owned(),
            Value::Array(rejection_details),
        );
    }
    extras
}

fn command_finalize_failed_lanes(route_manifest_version: i64) -> PublishLanesMetadata {
    PublishLanesMetadata::new(
        SourcePublishLaneMetadata::new(0, "failed", Some("command_finalize_failed")),
        IgnoredPublishLaneMetadata::new(
            0,
            0,
            0,
            "failed",
            None::<String>,
            Some("command_finalize_failed"),
        ),
        route_manifest_version,
    )
}

fn dropped_command_failed_lanes_with_counts(
    source_path_count: usize,
    ignored_path_count: usize,
    ignored_bytes: u64,
    route_manifest_version: i64,
) -> PublishLanesMetadata {
    PublishLanesMetadata::new(
        SourcePublishLaneMetadata::new(source_path_count, "dropped_command_failed", None::<String>),
        IgnoredPublishLaneMetadata::new(
            ignored_path_count,
            ignored_bytes,
            0,
            "dropped_command_failed",
            None::<String>,
            None::<String>,
        ),
        route_manifest_version,
    )
}

fn publish_lanes_from_publish_error(
    route_stats: CaptureRouteStats,
    route_manifest_version: i64,
) -> PublishLanesMetadata {
    let source_publish_status = if route_stats.gated_path_count == 0 {
        "empty"
    } else {
        "failed"
    };
    let source_drop_reason = if route_stats.gated_path_count == 0 {
        None
    } else {
        Some("publish_failed")
    };
    let ignored_publish_status = if route_stats.direct_path_count == 0 {
        "empty"
    } else {
        "failed"
    };
    let ignored_drop_reason = if route_stats.direct_path_count == 0 {
        None
    } else {
        Some("publish_failed")
    };
    publish_lanes_with_route_drop_summary(
        PublishLanesMetadata::new(
            SourcePublishLaneMetadata::new(
                route_stats.gated_path_count,
                source_publish_status,
                source_drop_reason,
            ),
            IgnoredPublishLaneMetadata::new(
                route_stats.direct_path_count,
                route_stats.direct_bytes,
                route_stats.direct_spooled_bytes,
                ignored_publish_status,
                None::<String>,
                ignored_drop_reason,
            ),
            route_manifest_version,
        ),
        route_stats.drop_path_count,
        route_stats.drop_reason_counts,
    )
}

fn publish_lanes_from_changeset(
    changeset: &ChangesetResult,
    route_stats: CaptureRouteStats,
    route_manifest_version: i64,
) -> PublishLanesMetadata {
    let source_path_count = route_stats.gated_path_count;
    let source_status = source_publish_status(changeset, source_path_count);
    let (ignored_status, ignored_mode, ignored_drop_reason) =
        ignored_publish_outcome(changeset, &route_stats, source_status);

    let publish_lanes = PublishLanesMetadata::new(
        SourcePublishLaneMetadata::new(source_path_count, source_status, None::<String>),
        IgnoredPublishLaneMetadata::new(
            route_stats.direct_path_count,
            route_stats.direct_bytes,
            route_stats.direct_spooled_bytes,
            ignored_status,
            ignored_mode,
            ignored_drop_reason,
        ),
        route_manifest_version,
    );
    publish_lanes_with_route_drop_summary(
        publish_lanes,
        route_stats.drop_path_count,
        route_stats.drop_reason_counts,
    )
}

fn publish_lanes_with_route_drop_summary(
    mut publish_lanes: PublishLanesMetadata,
    dropped_path_count: usize,
    drop_reason_counts: BTreeMap<String, usize>,
) -> PublishLanesMetadata {
    publish_lanes.routing.dropped_path_count = dropped_path_count;
    publish_lanes.routing.drop_reason_counts = drop_reason_counts;
    publish_lanes
}

fn source_publish_status(changeset: &ChangesetResult, source_path_count: usize) -> &'static str {
    if source_path_count == 0 {
        return "empty";
    }
    if changeset
        .files
        .iter()
        .any(|file| file.status == CommitStatus::AbortedVersion)
    {
        return "conflict";
    }
    if changeset
        .files
        .iter()
        .any(|file| file.status == CommitStatus::Failed)
    {
        return "failed";
    }
    if changeset.published_manifest_version.is_some() {
        "committed"
    } else {
        "accepted_noop"
    }
}

fn ignored_publish_outcome(
    changeset: &ChangesetResult,
    route_stats: &CaptureRouteStats,
    source_status: &str,
) -> (&'static str, Option<&'static str>, Option<String>) {
    if route_stats.direct_path_count == 0 {
        return ("empty", None, None);
    }
    if source_status == "conflict" {
        return (
            "dropped_due_to_source_conflict",
            None,
            Some("source_not_published".to_owned()),
        );
    }
    if route_rejection_failed(changeset, route_stats) {
        return ("failed", None, Some("publish_failed".to_owned()));
    }
    if source_status == "failed" {
        return (
            "dropped_due_to_source_conflict",
            None,
            Some("source_not_published".to_owned()),
        );
    }
    if let Some(reason) = route_stats.ignored_limit_drop_reason.as_deref() {
        return ("dropped_due_to_limits", None, Some(reason.to_owned()));
    }
    if changeset.success() {
        return ("published_lww", Some("direct_lww"), None);
    }
    ("failed", None, Some("publish_failed".to_owned()))
}

fn route_rejection_failed(changeset: &ChangesetResult, route_stats: &CaptureRouteStats) -> bool {
    if route_stats.drop_reason_counts.is_empty() {
        return false;
    }
    changeset.files.iter().any(|file| {
        file.status == CommitStatus::Failed
            && route_stats
                .drop_reason_counts
                .contains_key(file.message.as_str())
    })
}

fn publish_rejection_details_from_changeset(
    changeset: &ChangesetResult,
    publish_lanes: &PublishLanesMetadata,
) -> Vec<Value> {
    let publish_lanes = publish_lanes.to_value();
    changeset
        .files
        .iter()
        .filter(|file| !file.status.is_non_conflicting())
        .map(|file| {
            let reason = file.conflict_message(file.status.wire_str());
            json!({
                "path": file.path.as_str(),
                "status": file.status.wire_str(),
                "reason": reason,
                "message": reason,
                "route_validation": file.observed_state.as_deref() == Some("route_rejected"),
                "observed_state": file.observed_state.as_deref(),
                "observed_version": file.observed_version,
                "publish_lanes": publish_lanes.clone(),
            })
        })
        .collect()
}

fn insert_command_timings(
    timings: &mut WorkspaceTimings,
    changed_path_count: usize,
    capture_s: f64,
    occ_s: f64,
    elapsed_s: f64,
    include_api_total: bool,
) {
    for (key, value) in [
        (
            "resource.command_exec.changed_path_count",
            usize_to_f64_saturating(changed_path_count),
        ),
        ("command_exec.capture_upperdir_s", capture_s),
        ("command_exec.occ_apply_s", occ_s),
        ("command_exec.total_s", elapsed_s),
        ("sandbox.command.exec.dispatch_total_s", elapsed_s),
    ] {
        timings.insert(key.to_owned(), json!(value));
    }
    if include_api_total {
        timings.insert("sandbox.command.exec.total_s".to_owned(), json!(elapsed_s));
    }
}

fn base_timings(root: &Path) -> Result<WorkspaceTimings, WorkspaceApiError> {
    let manifest = service::active_manifest(root).map_err(|error| {
        WorkspaceApiError::new("daemon_command_workspace_error", error.to_string())
    })?;
    let mut timings = WorkspaceTimings::new();
    timings.insert(
        "resource.command_exec.changed_path_count".to_owned(),
        json!(0.0),
    );
    timings.insert(
        "resource.layer_stack.manifest_depth".to_owned(),
        json!(usize_to_f64_saturating(manifest.depth())),
    );
    // Tree stats are inserted only by the paths that actually walk a tree;
    // an absent key means "not sampled", never a fabricated zero walk.
    insert_cgroup_process_resource_timings(&mut timings);
    Ok(timings)
}

pub(crate) fn insert_cgroup_process_resource_timings(timings: &mut WorkspaceTimings) {
    let sampler_start = Instant::now();
    insert_cgroup_resource_timings(timings);
    insert_process_resource_timings(timings);
    timings.insert(
        "resource.sampler.cgroup_process_duration_us".to_owned(),
        json!(sampler_start.elapsed().as_micros()),
    );
}

fn insert_cgroup_resource_timings(timings: &mut WorkspaceTimings) {
    if let Ok(raw) = std::fs::read_to_string("/sys/fs/cgroup/cpu.stat") {
        for line in raw.lines() {
            let mut parts = line.split_whitespace();
            let Some(name) = parts.next() else {
                continue;
            };
            let Some(value) = parts.next().and_then(|raw| raw.parse::<f64>().ok()) else {
                continue;
            };
            timings.insert(format!("resource.cgroup.cpu_{name}"), json!(value));
        }
    }

    for (path, key) in [
        (
            "/sys/fs/cgroup/memory.current",
            "resource.cgroup.memory_current_bytes",
        ),
        (
            "/sys/fs/cgroup/memory.peak",
            "resource.cgroup.memory_peak_bytes",
        ),
        (
            "/sys/fs/cgroup/memory.swap.current",
            "resource.cgroup.memory_swap_current_bytes",
        ),
        (
            "/sys/fs/cgroup/memory.swap.peak",
            "resource.cgroup.memory_swap_peak_bytes",
        ),
    ] {
        if let Ok(raw) = std::fs::read_to_string(path) {
            if let Ok(value) = raw.trim().parse::<f64>() {
                timings.insert(key.to_owned(), json!(value));
            }
        }
    }

    if let Ok(raw) = std::fs::read_to_string("/sys/fs/cgroup/memory.events") {
        for line in raw.lines() {
            let mut parts = line.split_whitespace();
            let Some(name) = parts.next() else {
                continue;
            };
            let Some(value) = parts.next().and_then(|raw| raw.parse::<f64>().ok()) else {
                continue;
            };
            timings.insert(
                format!("resource.cgroup.memory_events_{name}"),
                json!(value),
            );
        }
    }

    if let Ok(raw) = std::fs::read_to_string("/sys/fs/cgroup/io.stat") {
        let mut totals = std::collections::BTreeMap::<&str, f64>::from([
            ("rbytes", 0.0),
            ("wbytes", 0.0),
            ("rios", 0.0),
            ("wios", 0.0),
            ("dbytes", 0.0),
            ("dios", 0.0),
        ]);
        for line in raw.lines() {
            for token in line.split_whitespace().skip(1) {
                let Some((name, raw_value)) = token.split_once('=') else {
                    continue;
                };
                let Some(total) = totals.get_mut(name) else {
                    continue;
                };
                if let Ok(value) = raw_value.parse::<f64>() {
                    *total += value;
                }
            }
        }
        for (name, value) in totals {
            timings.insert(format!("resource.cgroup.io_{name}"), json!(value));
        }
    }

    for (path, prefix) in [
        ("/sys/fs/cgroup/cpu.pressure", "cpu"),
        ("/sys/fs/cgroup/memory.pressure", "memory"),
        ("/sys/fs/cgroup/io.pressure", "io"),
    ] {
        if let Ok(raw) = std::fs::read_to_string(path) {
            insert_pressure_timings(timings, prefix, &raw);
        }
    }
}

fn insert_pressure_timings(timings: &mut WorkspaceTimings, prefix: &str, raw: &str) {
    for (key, value) in parse_pressure_metrics(prefix, raw) {
        timings.insert(format!("resource.cgroup.psi_{key}"), json!(value));
    }
}

fn parse_pressure_metrics(prefix: &str, raw: &str) -> std::collections::BTreeMap<String, f64> {
    let mut metrics = std::collections::BTreeMap::new();
    for line in raw.lines() {
        let mut tokens = line.split_whitespace();
        let Some(level @ ("some" | "full")) = tokens.next() else {
            continue;
        };
        for token in tokens {
            let Some((name @ ("avg10" | "avg60" | "avg300" | "total"), raw_value)) =
                token.split_once('=')
            else {
                continue;
            };
            if let Ok(value) = raw_value.parse::<f64>() {
                metrics.insert(format!("{prefix}_{level}_{name}"), value);
            }
        }
    }
    metrics
}

fn insert_process_resource_timings(timings: &mut WorkspaceTimings) {
    let Ok(status) = std::fs::read_to_string("/proc/self/status") else {
        return;
    };
    for line in status.lines() {
        let key = match line.split(':').next() {
            Some("VmRSS") => "resource.process.rss_bytes",
            Some("VmHWM") => "resource.process.max_rss_bytes",
            _ => continue,
        };
        if let Some(kib) = line
            .split_whitespace()
            .nth(1)
            .and_then(|value| value.parse::<f64>().ok())
        {
            timings.insert(key.to_owned(), json!(kib * 1024.0));
        }
    }
}

fn copy_runner_timings(timings: &mut WorkspaceTimings, runner_result: Option<&Value>) {
    let Some(runner_timings) = runner_result
        .and_then(|result| {
            result
                .get("payload")
                .and_then(|payload| payload.get("timings"))
                .or_else(|| result.get("timings"))
        })
        .and_then(Value::as_object)
    else {
        return;
    };
    for (key, value) in runner_timings {
        timings.entry(key.clone()).or_insert_with(|| value.clone());
    }
}

fn conflict_from_file(file: &FileResult) -> WorkspaceConflict {
    let reason = file.status.wire_str();
    WorkspaceConflict::path(reason, file.path.as_str(), file.conflict_message(reason))
}

fn conflict_message(file: &FileResult) -> &str {
    file.conflict_message(file.status.wire_str())
}

fn insert_tree_resource_timings(
    timings: &mut WorkspaceTimings,
    prefix: &str,
    stats: &TreeResourceStats,
) {
    let file_entries = stats.files.saturating_add(stats.symlinks);
    let entry_count = file_entries.saturating_add(stats.dirs);
    insert_resource_timing(
        timings,
        &format!("{prefix}_tree_exists"),
        entry_count.min(1),
    );
    insert_resource_timing(timings, &format!("{prefix}_tree_bytes"), stats.bytes);
    insert_resource_timing(timings, &format!("{prefix}_tree_file_count"), file_entries);
    insert_resource_timing(timings, &format!("{prefix}_tree_dir_count"), stats.dirs);
    insert_resource_timing(timings, &format!("{prefix}_tree_entry_count"), entry_count);
    insert_resource_timing(
        timings,
        &format!("{prefix}_tree_truncated"),
        u64::from(stats.truncated),
    );
    insert_resource_timing(
        timings,
        &format!("{prefix}_tree_read_error_count"),
        stats.read_error_count,
    );
    if let Some(path) = &stats.first_error_path {
        timings.insert(format!("{prefix}_tree_first_error_path"), json!(path));
    }
}

fn insert_resource_timing(timings: &mut WorkspaceTimings, key: &str, value: u64) {
    timings.insert(key.to_owned(), json!(u64_to_f64_saturating(value)));
}

fn finalize_error(error: impl std::fmt::Display) -> WorkspaceApiError {
    WorkspaceApiError::new("command_finalize_failed", error.to_string())
}

#[cfg(test)]
mod tests {
    use crate::command::CommandStatus;
    use workspace::overlay::tree::TreeResourceStats;

    use super::*;

    #[test]
    fn pressure_metrics_parse_some_and_full_levels() {
        let metrics = parse_pressure_metrics(
            "io",
            "some avg10=2.50 avg60=1.50 avg300=0.50 total=100\nfull avg10=0.75 avg60=0.25 avg300=0.05 total=9\n",
        );

        assert_eq!(metrics.get("io_some_avg10").copied(), Some(2.5));
        assert_eq!(metrics.get("io_some_total").copied(), Some(100.0));
        assert_eq!(metrics.get("io_full_avg300").copied(), Some(0.05));
        assert_eq!(metrics.get("io_full_total").copied(), Some(9.0));
    }

    #[test]
    fn tree_resource_timings_forward_truncation_marker() {
        let mut timings = crate::WorkspaceTimings::new();
        let stats = TreeResourceStats {
            files: 1,
            dirs: 1,
            symlinks: 0,
            bytes: 10,
            truncated: true,
            read_error_count: 1,
            first_error_path: Some("/tmp/missing".to_owned()),
        };

        insert_tree_resource_timings(&mut timings, "resource.command_exec.upperdir", &stats);

        assert_eq!(
            timings["resource.command_exec.upperdir_tree_truncated"],
            serde_json::json!(1.0)
        );
        assert_eq!(
            timings["resource.command_exec.upperdir_tree_read_error_count"],
            serde_json::json!(1.0)
        );
        assert_eq!(
            timings["resource.command_exec.upperdir_tree_first_error_path"],
            serde_json::json!("/tmp/missing")
        );
    }

    #[test]
    fn copy_runner_timings_reads_runner_payload_shape() {
        let mut timings = crate::WorkspaceTimings::new();
        let runner_result = serde_json::json!({
            "exit_code": 0,
            "payload": {
                "timings": {
                    "workspace.mount_s": 0.012,
                    "workspace.shell_spawn_s": 0.034
                }
            }
        });

        copy_runner_timings(&mut timings, Some(&runner_result));

        assert_eq!(timings["workspace.mount_s"], serde_json::json!(0.012));
        assert_eq!(timings["workspace.shell_spawn_s"], serde_json::json!(0.034));
    }

    #[test]
    fn discarded_response_without_manifest_version_still_reports_publish_lanes() {
        let response = discarded_response(
            WorkspaceKind::Host,
            FinalizeCommandRequest {
                runner_result: None,
                command_elapsed_s: 0.25,
                status: CommandStatus::Cancelled,
                exit_code: Some(130),
                stdout: String::new(),
                stderr: String::new(),
                command_id: Some("cmd_discarded_no_manifest".to_owned()),
            },
            None,
        )
        .to_wire_value();

        assert_eq!(
            response["publish_lanes"]["source"]["publish_status"],
            "dropped_command_failed"
        );
        assert_eq!(
            response["publish_lanes"]["ignored"]["publish_status"],
            "dropped_command_failed"
        );
        assert_eq!(
            response["publish_lanes"]["routing"]["route_manifest_version"],
            0
        );
    }

    #[test]
    fn finalization_error_response_without_manifest_version_still_reports_publish_lanes() {
        let response = finalization_error_response(
            WorkspaceKind::Host,
            FinalizeCommandRequest {
                runner_result: None,
                command_elapsed_s: 0.25,
                status: CommandStatus::Ok,
                exit_code: Some(0),
                stdout: String::new(),
                stderr: String::new(),
                command_id: Some("cmd_finalize_error_no_manifest".to_owned()),
            },
            None,
            "capture failed",
        )
        .to_wire_value();

        assert_eq!(response["status"], "ok");
        assert_eq!(response["success"], false);
        assert_eq!(
            response["publish_lanes"]["source"]["publish_status"],
            "failed"
        );
        assert_eq!(
            response["publish_lanes"]["ignored"]["publish_status"],
            "failed"
        );
        assert_eq!(
            response["publish_lanes"]["routing"]["route_manifest_version"],
            0
        );
    }
}
