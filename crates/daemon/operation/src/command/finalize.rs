use std::path::Path;
use std::time::Instant;

use layerstack::service;
use serde_json::{json, Map, Value};
use trace::usize_to_f64_saturating;
use workspace::overlay::capture::capture_upperdir;
use workspace::overlay::tree::TreeResourceStats;
use workspace::profile::WorkspaceModeContext;

use super::contract::{
    u64_to_f64_saturating, CommandMetadata, CommandResponse, IgnoredPublishLaneMetadata,
    PublishLanesMetadata, SourcePublishLaneMetadata, PUBLISH_LANES_METADATA_KEY,
};
use super::outcome::{
    ChangedPathKinds, FinalizeCommandRequest, MutationSource, WorkspaceApiError, WorkspaceConflict,
    WorkspaceKind, WorkspaceTimings,
};
use crate::core::changed_path_kind_pairs;
use crate::{CommandId, MutationCore};

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
    let mut extras = Map::new();
    extras.insert(
        PUBLISH_LANES_METADATA_KEY.to_owned(),
        publish_lanes.to_value(),
    );
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
