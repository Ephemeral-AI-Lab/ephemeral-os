use std::path::Path;

use command::process::{CommandFinalResponsePersistence, CommandPersistenceOutcome, KillReason};
use serde_json::{json, Map, Value};
use trace::{
    EventRecord, RequestId, SpanKind, SpanRecord, SpanStatus, SpanUid, TraceId, TraceKind,
    TraceLink, TraceLinkKind, TraceRecord,
};

use super::contract::{
    CommandResponse, CommandStatus, PUBLISH_LANES_METADATA_KEY,
    PUBLISH_REJECTION_DETAILS_METADATA_KEY,
};
use super::finalize::insert_cgroup_process_resource_timings;
use super::outcome::WorkspaceTimings;
use super::registry::{CommandTraceOrigin, CompletionBufferEviction};

#[derive(Debug, Clone, PartialEq)]
pub struct CommandTraceEvent {
    pub name: &'static str,
    pub details: Value,
}

impl CommandTraceEvent {
    #[must_use]
    pub fn new(name: &'static str, details: Value) -> Self {
        Self { name, details }
    }

    #[must_use]
    pub fn artifact_written(artifact: &'static str, path: &Path, bytes: usize) -> Self {
        Self::new(
            "artifact_written",
            json!({
                "artifact": artifact,
                "path": path.display().to_string(),
                "bytes": bytes,
            }),
        )
    }

    #[must_use]
    pub fn artifact_failed(artifact: &'static str, path: &Path, error: impl ToString) -> Self {
        Self::new(
            "artifact_failed",
            json!({
                "artifact": artifact,
                "path": path.display().to_string(),
                "error": error.to_string(),
            }),
        )
    }
}

pub(super) struct FinalizedCommand {
    pub(super) response: CommandResponse,
    pub(super) trace: CommandFinalizeTraceFacts,
}

pub(super) struct CommandFinalizeTraceFacts {
    pub(super) trace_origin: CommandTraceOrigin,
    pub(super) command_id: String,
    pub(super) caller_id: String,
    pub(super) status: CommandStatus,
    pub(super) exit_code: Option<i64>,
    pub(super) signal: Option<i32>,
    pub(super) kill: Option<KillReason>,
    pub(super) command_elapsed_s: f64,
    pub(super) persistence: CommandPersistenceOutcome,
    pub(super) publish_completion: bool,
    pub(super) evictions: Vec<CompletionBufferEviction>,
    pub(super) publish_lanes: Option<Value>,
    pub(super) publish_rejection_details: Vec<Value>,
}

/// Trace events embed at most this many changed paths; the full list lives in
/// the response payload, and the count is always exact.
const CHANGED_PATHS_EVENT_HEAD: usize = 32;

pub(super) fn command_response_trace_events(response: &CommandResponse) -> Vec<CommandTraceEvent> {
    let Some(finalized) = response.finalized.as_ref() else {
        return Vec::new();
    };
    let changed_path_count = finalized.core.changed_paths.len();
    let changed_paths_head: Vec<&String> = finalized
        .core
        .changed_paths
        .iter()
        .take(CHANGED_PATHS_EVENT_HEAD)
        .collect();
    let changed_paths_truncated = changed_path_count > CHANGED_PATHS_EVENT_HEAD;
    let command_id = response.command_id.as_ref().map(ToString::to_string);
    let workspace = finalized.workspace.as_str();
    let capture_duration_s = finalized
        .core
        .timings
        .get("command_exec.capture_upperdir_s")
        .cloned()
        .unwrap_or(Value::Null);
    let mount_duration_s = finalized
        .core
        .timings
        .get("workspace.mount_s")
        .cloned()
        .unwrap_or(Value::Null);
    let unmount_duration_s = finalized
        .core
        .timings
        .get("workspace.unmount_s")
        .cloned()
        .unwrap_or(Value::Null);
    let runner_timings = runner_timings(&finalized.core.timings);
    let layer_count = finalized
        .core
        .timings
        .get("resource.layer_stack.manifest_depth")
        .cloned()
        .unwrap_or(Value::Null);
    let mut events = vec![
        CommandTraceEvent::new(
            "overlay_workspace_prepared",
            json!({
                "command_id": command_id,
                "workspace": workspace,
                "layer_count": layer_count,
            }),
        ),
        CommandTraceEvent::new(
            "overlay_mount_started",
            json!({
                "command_id": command_id,
                "workspace": workspace,
                "layer_count": layer_count,
            }),
        ),
        CommandTraceEvent::new(
            "overlay_mount_finished",
            json!({
                "command_id": command_id,
                "workspace": workspace,
                "layer_count": layer_count,
                "duration_s": mount_duration_s,
                "upperdir_empty_bytes": 0,
            }),
        ),
        CommandTraceEvent::new(
            "overlay_unmount_finished",
            json!({
                "command_id": command_id,
                "workspace": workspace,
                "duration_s": unmount_duration_s,
            }),
        ),
        CommandTraceEvent::new(
            "overlay_capture_started",
            json!({
                "command_id": command_id,
                "workspace": workspace,
            }),
        ),
        CommandTraceEvent::new(
            "overlay_capture_finished",
            json!({
                "command_id": command_id,
                "workspace": workspace,
                "duration_s": capture_duration_s,
                "changed_path_count": changed_path_count,
                "changed_paths": changed_paths_head,
                "changed_paths_truncated": changed_paths_truncated,
            }),
        ),
        CommandTraceEvent::new(
            "changed_paths_recorded",
            json!({
                "command_id": command_id,
                "workspace": workspace,
                "changed_path_count": changed_path_count,
                "changed_paths": changed_paths_head,
                "changed_paths_truncated": changed_paths_truncated,
            }),
        ),
        CommandTraceEvent::new(
            "runner_timing",
            json!({
                "command_id": command_id,
                "workspace": workspace,
                "timings": runner_timings,
            }),
        ),
        CommandTraceEvent::new(
            "response_meta",
            json!({
                "command_id": command_id,
                "status": response.status.as_str(),
                "exit_code": response.exit_code,
                "workspace": workspace,
                "success": finalized.core.success,
                "changed_path_count": changed_path_count,
            }),
        ),
    ];
    if let Some(publish_lanes) = finalized.extras.get(PUBLISH_LANES_METADATA_KEY) {
        events.push(CommandTraceEvent::new(
            "command.publish_lanes_decided",
            publish_lanes.clone(),
        ));
    }
    events.extend(
        publish_rejection_details_from_extras(&finalized.extras)
            .into_iter()
            .map(|details| CommandTraceEvent::new("command.publish_rejection_detail", details)),
    );
    events
}

pub(super) fn publish_rejection_details_from_extras(extras: &Map<String, Value>) -> Vec<Value> {
    extras
        .get(PUBLISH_REJECTION_DETAILS_METADATA_KEY)
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .cloned()
        .collect()
}

fn runner_timings(timings: &WorkspaceTimings) -> Map<String, Value> {
    const KEYS: &[&str] = &[
        "workspace.namespace_enter_s",
        "workspace.namespace_setsid_s",
        "workspace.namespace_unshare_s",
        "workspace.namespace_uid_gid_map_s",
        "workspace.namespace_mount_private_s",
        "workspace.cgroup_join_s",
        "workspace.setns_join_s",
        "workspace.mount_s",
        "workspace.overlay_mount_s",
        "workspace.shell_prepare_s",
        "workspace.shell_spawn_s",
        "workspace.shell_wait_s",
        "workspace.shell_wait_root_exit_s",
        "workspace.shell_wait_post_root_drain_s",
        "workspace.shell_wait_child_try_wait_s",
        "workspace.shell_wait_proc_scan_s",
        "workspace.shell_wait_proc_scan_count",
        "workspace.shell_wait_poll_count",
        "workspace.shell_wait_poll_sleep_s",
        "workspace.plugin_prepare_s",
        "workspace.plugin_spawn_s",
        "workspace.plugin_wait_s",
        "workspace.tool_s",
        "workspace.unmount_s",
    ];
    KEYS.iter()
        .filter_map(|key| {
            timings
                .get(*key)
                .map(|value| ((*key).to_owned(), value.clone()))
        })
        .collect()
}

pub(super) fn command_process_wait_resource_stats_event(
    phase: &'static str,
    timings: &WorkspaceTimings,
) -> CommandTraceEvent {
    let mut cpu = Map::new();
    let mut memory = Map::new();
    let mut io = Map::new();
    let mut psi = Map::new();
    let mut process = Map::new();
    for (key, value) in timings {
        if let Some(name) = key.strip_prefix("resource.cgroup.cpu_") {
            cpu.insert(name.to_owned(), value.clone());
        } else if let Some(name) = key.strip_prefix("resource.cgroup.memory_") {
            memory.insert(name.to_owned(), value.clone());
        } else if let Some(name) = key.strip_prefix("resource.cgroup.io_") {
            io.insert(name.to_owned(), value.clone());
        } else if let Some(name) = key.strip_prefix("resource.cgroup.psi_") {
            psi.insert(name.to_owned(), value.clone());
        } else if let Some(name) = key.strip_prefix("resource.process.") {
            process.insert(name.to_owned(), value.clone());
        }
    }
    let cgroup_available =
        !(cpu.is_empty() && memory.is_empty() && io.is_empty() && psi.is_empty());
    let process_available = !process.is_empty();
    let sampler_duration_us = timings
        .get("resource.sampler.cgroup_process_duration_us")
        .cloned()
        .unwrap_or(Value::Null);
    CommandTraceEvent::new(
        "resource_stats",
        json!({
            "meta": {
                "stats_kind": "cgroup_process",
                "phase": phase,
                "source": "command.process.wait",
                "source_available": cgroup_available || process_available,
                "read_error": (!(cgroup_available || process_available)).then_some("resource timings unavailable on this platform or request path"),
                "sampler_duration_us": sampler_duration_us,
            },
            "cgroup": {
                "source_available": cgroup_available,
                "cpu": cpu,
                "memory": memory,
                "io": io,
                "psi": psi,
            },
            "process": {
                "source_available": process_available,
                "gauges": process,
            },
        }),
    )
}

pub(super) fn command_process_wait_tree_resource_stats_events(
    timings: &WorkspaceTimings,
) -> Vec<CommandTraceEvent> {
    let mut groups = std::collections::BTreeMap::<String, Map<String, Value>>::new();
    for (key, value) in timings {
        let Some(key) = key.strip_prefix("resource.") else {
            continue;
        };
        let Some((source, metric)) = key.split_once("_tree_") else {
            continue;
        };
        groups
            .entry(source.to_owned())
            .or_default()
            .insert(metric.to_owned(), value.clone());
    }
    groups
        .into_iter()
        .map(|(source, mut tree)| {
            // The walk duration is recorded beside the walk that paid for it;
            // tree stats are never part of a before/after gauge pair.
            let sampler_duration_us = tree.remove("sampler_duration_us").unwrap_or(Value::Null);
            CommandTraceEvent::new(
                "resource_stats",
                json!({
                    "meta": {
                        "stats_kind": "tree",
                        "phase": "after",
                        "source": format!("resource.{source}"),
                        "source_available": true,
                        "sampler_duration_us": sampler_duration_us,
                    },
                    "tree": tree,
                }),
            )
        })
        .collect()
}

pub(super) fn command_process_wait_host_resource_stats_event(
    phase: &'static str,
    timings: &WorkspaceTimings,
) -> CommandTraceEvent {
    let mut process = Map::new();
    for (key, value) in timings {
        if let Some(name) = key.strip_prefix("resource.process.") {
            process.insert(name.to_owned(), value.clone());
        }
    }
    let source_available = !process.is_empty();
    let sampler_duration_us = timings
        .get("resource.sampler.cgroup_process_duration_us")
        .cloned()
        .unwrap_or(Value::Null);
    CommandTraceEvent::new(
        "resource_stats",
        json!({
            "meta": {
                "stats_kind": "host",
                "phase": phase,
                "source": "daemon.process",
                "source_available": source_available,
                "read_error": (!source_available).then_some("daemon process gauges unavailable on this platform"),
                "sampler_duration_us": sampler_duration_us,
            },
            "host": {
                "process": process,
            },
        }),
    )
}

/// Attach the command's before/after `command.process.wait` resource pair to
/// its finalize record when the pair never rode a request sidecar. The
/// "after" sample prefers the gauges captured during finalization; discarded
/// work (empty timings) gets a fresh sample so the pair stays complete.
pub(super) fn append_resource_pair_to_record(
    record: &mut TraceRecord,
    before: &WorkspaceTimings,
    response: &CommandResponse,
) {
    let after = response
        .finalized
        .as_ref()
        .map(|finalized| finalized.core.timings.clone())
        .filter(|timings| timings.keys().any(|key| key.starts_with("resource.")))
        .unwrap_or_else(|| {
            let mut timings = WorkspaceTimings::new();
            insert_cgroup_process_resource_timings(&mut timings);
            timings
        });
    for event in [
        command_process_wait_resource_stats_event("before", before),
        command_process_wait_host_resource_stats_event("before", before),
        command_process_wait_resource_stats_event("after", &after),
        command_process_wait_host_resource_stats_event("after", &after),
    ] {
        let mut event_record =
            EventRecord::new(record.root_span_id, event.name, "resource", event.details);
        event_record.at_unix_ms = record.finished_at_unix_ms;
        record.events.push(event_record);
    }
}

pub(super) fn command_finalize_trace_record(facts: &CommandFinalizeTraceFacts) -> TraceRecord {
    let now = unix_now_ms();
    let wait_duration_us = duration_us_from_secs(facts.command_elapsed_s);
    let mut span = SpanRecord::new(
        SpanUid::ROOT,
        None,
        "command.finalize",
        SpanKind::CommandFinalize,
        json!({
            "command_id": facts.command_id,
            "caller_id": facts.caller_id,
            "origin_request_id": facts.trace_origin.request_id,
            "publish_completion": facts.publish_completion,
        }),
    );
    span.started_at_unix_ms = now;
    span.finished_at_unix_ms = now;
    span.status = Some(command_span_status(facts.status));
    let mut wait_span = SpanRecord::new(
        SpanUid::new(2),
        Some(SpanUid::ROOT),
        "command.process.wait",
        SpanKind::CommandProcessWait,
        json!({
            "command_id": facts.command_id,
            "caller_id": facts.caller_id,
            "elapsed_s": facts.command_elapsed_s,
            "kill_reason": facts.kill.map(kill_reason_label),
        }),
    );
    wait_span.started_at_unix_ms = now.saturating_sub(wait_duration_us / 1_000);
    wait_span.finished_at_unix_ms = now;
    wait_span.duration_us = wait_duration_us;
    wait_span.status = Some(command_span_status(facts.status));

    let mut events = vec![
        EventRecord::new(
            SpanUid::new(2),
            "exit_taken",
            "command",
            json!({
                "command_id": facts.command_id,
                "exit_code": facts.exit_code,
                "signal": facts.signal,
                "kill_reason": facts.kill.map(kill_reason_label),
            }),
        ),
        EventRecord::new(
            SpanUid::ROOT,
            "finalized",
            "command",
            json!({
                "command_id": facts.command_id,
                "caller_id": facts.caller_id,
                "status": facts.status.as_str(),
                "exit_code": facts.exit_code,
                "signal": facts.signal,
                "kill_reason": facts.kill.map(kill_reason_label),
                "elapsed_s": facts.command_elapsed_s,
                "publish_completion": facts.publish_completion,
            }),
        ),
    ];
    if let Some(publish_lanes) = &facts.publish_lanes {
        events.push(EventRecord::new(
            SpanUid::ROOT,
            "command.publish_lanes_decided",
            "command",
            publish_lanes.clone(),
        ));
    }
    events.extend(
        facts
            .publish_rejection_details
            .iter()
            .cloned()
            .map(|details| {
                EventRecord::new(
                    SpanUid::ROOT,
                    "command.publish_rejection_detail",
                    "command",
                    details,
                )
            }),
    );
    if let Some(kill) = facts.kill {
        events.push(EventRecord::new(
            SpanUid::new(2),
            kill_reason_label(kill),
            "command",
            json!({
                "command_id": facts.command_id,
                "exit_code": facts.exit_code,
                "signal": facts.signal,
                "elapsed_s": facts.command_elapsed_s,
            }),
        ));
    }
    events.extend(facts.evictions.iter().map(|eviction| {
        EventRecord::new(
            SpanUid::ROOT,
            "completion_buffer_evicted",
            "command",
            json!({
                "command_id": eviction.command_id,
                "seq": eviction.seq,
                "max_entries": eviction.max_entries,
            }),
        )
    }));
    append_persistence_events(&mut events, &facts.persistence);
    for event in &mut events {
        event.at_unix_ms = now;
    }

    let mut record = TraceRecord::new(trace_id_from_origin(&facts.trace_origin), SpanUid::ROOT);
    record.request_id = facts
        .trace_origin
        .request_id
        .as_ref()
        .and_then(|request_id| RequestId::parse(request_id.clone()).ok());
    record.kind = TraceKind::CommandFinalize;
    record.started_at_unix_ms = now;
    record.finished_at_unix_ms = now;
    record.spans.push(span);
    record.spans.push(wait_span);
    record.events = events;
    record.links.push(TraceLink {
        kind: TraceLinkKind::Command,
        value: facts.command_id.clone(),
    });
    record
}

pub(super) fn active_command_advance_trace_record(
    live_count: usize,
    timed_out_commands: Vec<String>,
    finalized_commands: Vec<String>,
) -> TraceRecord {
    let now = unix_now_ms();
    let mut span = SpanRecord::new(
        SpanUid::ROOT,
        None,
        "command.active.advance",
        SpanKind::CommandProcessWait,
        json!({
            "live_count": live_count,
            "timed_out_count": timed_out_commands.len(),
            "finalized_count": finalized_commands.len(),
        }),
    );
    span.started_at_unix_ms = now;
    span.finished_at_unix_ms = now;
    span.status = Some(SpanStatus::Ok);
    let mut event = EventRecord::new(
        SpanUid::ROOT,
        "advance_finished",
        "command",
        json!({
            "live_count": live_count,
            "timed_out_commands": timed_out_commands,
            "finalized_commands": finalized_commands,
        }),
    );
    event.at_unix_ms = now;

    let mut record = TraceRecord::new(TraceId::new(), SpanUid::ROOT);
    record.kind = TraceKind::ActiveCommandAdvance;
    record.started_at_unix_ms = now;
    record.finished_at_unix_ms = now;
    record.spans.push(span);
    record.events.push(event);
    record
}

fn append_persistence_events(
    events: &mut Vec<EventRecord>,
    persistence: &CommandPersistenceOutcome,
) {
    match &persistence.final_response {
        Some(CommandFinalResponsePersistence::Persisted { path, bytes }) => {
            events.push(EventRecord::new(
                SpanUid::ROOT,
                "final_persisted",
                "command",
                json!({
                    "path": path.display().to_string(),
                    "bytes": bytes,
                }),
            ));
        }
        Some(CommandFinalResponsePersistence::Failed { path, error }) => {
            events.push(EventRecord::new(
                SpanUid::ROOT,
                "final_persist_failed",
                "command",
                json!({
                    "path": path.display().to_string(),
                    "error": error,
                }),
            ));
        }
        None => {}
    }

    if let Some(error) = &persistence.transcript_error {
        events.push(EventRecord::new(
            SpanUid::ROOT,
            "transcript_failed",
            "command",
            json!({
                "path": error.path.display().to_string(),
                "error": error.error,
            }),
        ));
    }
}

fn trace_id_from_origin(origin: &CommandTraceOrigin) -> TraceId {
    origin
        .trace_id
        .as_ref()
        .and_then(|trace_id| TraceId::parse(trace_id.clone()).ok())
        .unwrap_or_default()
}

fn command_span_status(status: CommandStatus) -> SpanStatus {
    match status {
        CommandStatus::Running | CommandStatus::Ok => SpanStatus::Ok,
        CommandStatus::Cancelled => SpanStatus::Cancelled,
        CommandStatus::Error => SpanStatus::Error,
        CommandStatus::TimedOut => SpanStatus::TimedOut,
    }
}

fn kill_reason_label(reason: KillReason) -> &'static str {
    match reason {
        KillReason::Cancelled => "cancelled",
        KillReason::TimedOut => "timed_out",
    }
}

pub(super) fn unix_now_ms() -> u64 {
    let millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    u64::try_from(millis).unwrap_or(u64::MAX)
}

fn duration_us_from_secs(seconds: f64) -> u64 {
    if !seconds.is_finite() || seconds <= 0.0 {
        return 0;
    }
    let micros = seconds * 1_000_000.0;
    if micros >= u64::MAX as f64 {
        u64::MAX
    } else {
        micros.round() as u64
    }
}
