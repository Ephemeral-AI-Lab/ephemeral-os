//! Trace-emission helpers for the plugin op adapters: the per-phase
//! `record_*` events for oneshot overlay runs, plugin ensure/status outcomes,
//! and the stale-PPC background root. These mutate no response shape; they only
//! splice trace events onto the request (or background spool).

use serde_json::{json, Map, Value};

use operation::plugin::{
    EnsureReady, PluginOverlayOutcome, PluginRuntimeError, PluginSetupReport, PpcError,
    PpcTraceEvent, ServiceProcessStatus, StatusOutcome,
};

use crate::DispatchContext;

pub(crate) fn record_plugin_overlay_started(
    context: &DispatchContext<'_>,
    op: &str,
    invocation_id: &str,
    overlay: &PluginOverlayOutcome,
) {
    context.record_trace_event(
        "plugin",
        "overlay_started",
        json!({
            "op": op,
            "invocation_id": invocation_id,
            "layer_stack_root": overlay.layer_stack_root,
        }),
    );
}

pub(crate) fn record_plugin_overlay_workspace_prepared(
    context: &DispatchContext<'_>,
    op: &str,
    invocation_id: &str,
    overlay: &PluginOverlayOutcome,
) {
    context.record_trace_event(
        "overlay",
        "workspace_prepared",
        json!({
            "op": op,
            "invocation_id": invocation_id,
            "source": "plugin_oneshot_overlay",
            "layer_stack_root": overlay.layer_stack_root,
            "layer_count": overlay.layer_count,
        }),
    );
}

pub(crate) fn record_plugin_overlay_mount_started(
    context: &DispatchContext<'_>,
    op: &str,
    invocation_id: &str,
    overlay: &PluginOverlayOutcome,
) {
    context.record_trace_event(
        "overlay",
        "mount_started",
        json!({
            "op": op,
            "invocation_id": invocation_id,
            "source": "plugin_oneshot_overlay",
            "layer_stack_root": overlay.layer_stack_root,
            "layer_count": overlay.layer_count,
        }),
    );
}

pub(crate) fn record_plugin_overlay_resource_stats(
    context: &DispatchContext<'_>,
    phase: &'static str,
    timings: &Map<String, Value>,
) {
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
    context.record_trace_event(
        "resource",
        "resource_stats",
        json!({
            "meta": {
                "stats_kind": "cgroup_process",
                "phase": phase,
                "source": "plugin.overlay.run",
                "source_available": cgroup_available || process_available,
                "read_error": (!(cgroup_available || process_available)).then_some("resource timings unavailable on this platform or request path"),
                "sampler_duration_us": sampler_duration_us,
                "inflight_requests": context
                    .invocation_registry()
                    .map_or(0, crate::invocation_registry::InFlightRegistry::inflight_count),
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
    );
}

pub(crate) fn record_plugin_overlay_host_resource_stats(
    context: &DispatchContext<'_>,
    phase: &'static str,
    timings: &Map<String, Value>,
) {
    let mut process = Map::new();
    for (key, value) in timings {
        if let Some(name) = key.strip_prefix("resource.process.") {
            process.insert(name.to_owned(), value.clone());
        }
    }
    let source_available = !process.is_empty();
    context.record_trace_event(
        "resource",
        "resource_stats",
        json!({
            "meta": {
                "stats_kind": "host",
                "phase": phase,
                "source": "daemon.process",
                "source_available": source_available,
                "read_error": (!source_available).then_some("daemon process gauges unavailable on this platform"),
                "sampler_duration_us": 0,
                "inflight_requests": context
                    .invocation_registry()
                    .map_or(0, crate::invocation_registry::InFlightRegistry::inflight_count),
            },
            "host": {
                "process": process,
            },
        }),
    );
}

pub(crate) fn record_plugin_overlay_mount_finished(
    context: &DispatchContext<'_>,
    op: &str,
    invocation_id: &str,
    overlay: &PluginOverlayOutcome,
) {
    let mount_s = runner_timing(&overlay.runner, "workspace.mount_s");
    let fsconfig_calls = runner_timing(&overlay.runner, "workspace.fsconfig_calls");
    let duration_us = mount_s.map(seconds_to_micros_saturating);
    context.record_trace_event(
        "overlay",
        "mount_finished",
        json!({
            "op": op,
            "invocation_id": invocation_id,
            "source": "plugin_oneshot_overlay",
            "layer_stack_root": overlay.layer_stack_root,
            "success": mount_s.is_some(),
            "duration_s": mount_s,
            "duration_available": mount_s.is_some(),
            "layer_count": overlay.layer_count,
            "fsconfig_calls": fsconfig_calls,
            "fsconfig_calls_available": fsconfig_calls.is_some(),
            "upperdir_empty_bytes": 0,
        }),
    );
    context.record_trace_event(
        "resource",
        "resource_stats",
        json!({
            "meta": {
                "stats_kind": "mount_cost",
                "phase": "after",
                "source": "plugin.overlay.mount",
                "source_available": mount_s.is_some(),
                "read_error": mount_s.is_none().then_some("overlay mount timing unavailable"),
                "sampler_duration_us": 0,
                "inflight_requests": context
                    .invocation_registry()
                    .map_or(0, crate::invocation_registry::InFlightRegistry::inflight_count),
            },
            "mount": {
                "duration_us": duration_us,
                "duration_available": duration_us.is_some(),
                "layer_count": overlay.layer_count,
                "fsconfig_calls": fsconfig_calls,
                "fsconfig_calls_available": fsconfig_calls.is_some(),
                "upperdir_empty_bytes": 0,
            },
        }),
    );
}

pub(crate) fn record_plugin_overlay_unmount_finished(
    context: &DispatchContext<'_>,
    op: &str,
    invocation_id: &str,
    overlay: &PluginOverlayOutcome,
) {
    let unmount_s = runner_timing(&overlay.runner, "workspace.unmount_s");
    let unmount_error = overlay
        .runner
        .payload
        .get("workspace_unmount_error")
        .and_then(Value::as_str);
    context.record_trace_event(
        "overlay",
        "unmount_finished",
        json!({
            "op": op,
            "invocation_id": invocation_id,
            "source": "plugin_oneshot_overlay",
            "layer_stack_root": overlay.layer_stack_root,
            "success": unmount_s.is_some() && unmount_error.is_none(),
            "duration_s": unmount_s,
            "duration_available": unmount_s.is_some(),
            "layer_count": overlay.layer_count,
            "error": unmount_error,
        }),
    );
}

fn runner_timing(runner: &namespace::protocol::RunResult, key: &str) -> Option<f64> {
    runner
        .payload
        .get("timings")
        .and_then(Value::as_object)
        .and_then(|timings| timings.get(key))
        .and_then(Value::as_f64)
}

fn seconds_to_micros_saturating(seconds: f64) -> u64 {
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

pub(crate) fn record_plugin_overlay_capture_started(
    context: &DispatchContext<'_>,
    op: &str,
    invocation_id: &str,
    overlay: &PluginOverlayOutcome,
) {
    context.record_trace_event(
        "overlay",
        "capture_started",
        json!({
            "op": op,
            "invocation_id": invocation_id,
            "source": "plugin_oneshot_overlay",
            "layer_stack_root": overlay.layer_stack_root,
        }),
    );
}

pub(crate) fn record_plugin_overlay_capture_finished(
    context: &DispatchContext<'_>,
    op: &str,
    invocation_id: &str,
    overlay: &PluginOverlayOutcome,
) {
    context.record_trace_event(
        "overlay",
        "capture_finished",
        json!({
            "op": op,
            "invocation_id": invocation_id,
            "source": "plugin_oneshot_overlay",
            "layer_stack_root": overlay.layer_stack_root,
            "success": true,
            "duration_s": overlay.capture_s,
            "changed_path_count": overlay.path_kinds.len(),
            "bytes": overlay.upperdir_stats.bytes,
            "file_count": overlay.upperdir_stats.files,
            "dir_count": overlay.upperdir_stats.dirs,
            "symlink_count": overlay.upperdir_stats.symlinks,
            "entry_count": overlay
                .upperdir_stats
                .files
                .saturating_add(overlay.upperdir_stats.dirs)
                .saturating_add(overlay.upperdir_stats.symlinks),
            "truncated": overlay.upperdir_stats.truncated,
            "read_error_count": overlay.upperdir_stats.read_error_count,
            "failing_path": overlay.upperdir_stats.first_error_path.clone(),
        }),
    );
}

pub(crate) fn record_occ_changeset_trace_events(
    context: &DispatchContext<'_>,
    changeset: &layerstack::ChangesetResult,
) {
    for event in changeset.trace_events() {
        context.record_trace_event(event.module, event.name, event.details);
    }
}

pub(crate) fn record_plugin_overlay_lease_release_failed(
    context: &DispatchContext<'_>,
    op: &str,
    invocation_id: &str,
    overlay: &PluginOverlayOutcome,
) {
    let Some(error) = overlay.lease_release_error.as_deref() else {
        return;
    };
    context.record_trace_event(
        "layer_stack",
        "lease_release_failed",
        json!({
            "op": op,
            "invocation_id": invocation_id,
            "reason": "plugin_overlay_release_failed",
            "error": error,
        }),
    );
}

pub(crate) fn record_ppc_trace_events(context: &DispatchContext<'_>, events: Vec<PpcTraceEvent>) {
    for event in events {
        context.record_trace_event(event.module, event.name, event.details);
    }
}

/// PPC facts that accumulated with no plugin op in flight (orphan replies,
/// refused callbacks) become a standalone `PluginService` background root
/// instead of being dropped or misattributed to the next request's trace.
pub(crate) fn push_stale_ppc_background_root(events: Vec<PpcTraceEvent>) {
    use trace::{EventRecord, SpanKind, SpanRecord, SpanUid, TraceId, TraceKind, TraceRecord};
    if events.is_empty() {
        return;
    }
    let now = crate::trace::now_ms();
    let mut span = SpanRecord::new(
        SpanUid::ROOT,
        None,
        "plugin.service",
        SpanKind::Plugin,
        json!({"event_count": events.len(), "source": "stale_ppc_drain"}),
    );
    span.started_at_unix_ms = now;
    span.finished_at_unix_ms = now;
    let mut record = TraceRecord::new(TraceId::new(), SpanUid::ROOT);
    record.kind = TraceKind::PluginService;
    record.started_at_unix_ms = now;
    record.finished_at_unix_ms = now;
    record.spans.push(span);
    for event in events {
        let mut event_record =
            EventRecord::new(SpanUid::ROOT, event.name, event.module, event.details);
        event_record.at_unix_ms = now;
        record.events.push(event_record);
    }
    crate::trace::push_background_record(record);
}

pub(crate) fn record_plugin_overlay_finished(
    context: &DispatchContext<'_>,
    op: &str,
    invocation_id: &str,
    overlay: &PluginOverlayOutcome,
    response: &Value,
    adapter_error: Option<&crate::error::DaemonError>,
) {
    context.record_trace_event(
        "plugin",
        "overlay_finished",
        json!({
            "op": op,
            "invocation_id": invocation_id,
            "layer_stack_root": overlay.layer_stack_root,
            "success": response.get("success").and_then(Value::as_bool).unwrap_or(false),
            "status": response.get("status").and_then(Value::as_str),
            "error_kind": response
                .get("error")
                .and_then(|error| error.get("kind"))
                .and_then(Value::as_str),
            "adapter_error": adapter_error.map(ToString::to_string),
            "worker_exit_code": overlay.runner.exit_code,
            "changed_path_count": overlay.path_kinds.len(),
            "published_manifest_version": overlay.changeset.published_manifest_version,
            "lease_acquire_s": overlay.lease_acquire_s,
            "lease_release_error": overlay.lease_release_error.as_deref(),
            "capture_s": overlay.capture_s,
            "occ_s": overlay.occ_s,
            "upperdir_files": overlay.upperdir_stats.files,
            "upperdir_dirs": overlay.upperdir_stats.dirs,
            "upperdir_symlinks": overlay.upperdir_stats.symlinks,
            "upperdir_bytes": overlay.upperdir_stats.bytes,
        }),
    );
}

pub(crate) fn record_plugin_ensure_trace_events(
    context: &DispatchContext<'_>,
    ready: &EnsureReady,
) {
    context.record_trace_event(
        "plugin",
        "package_checked",
        json!({
            "plugin": ready.plugin_id,
            "digest": ready.digest,
            "active": ready.package.active,
            "needs_upload": ready.package.needs_upload,
            "package_root": ready
                .package
                .package_root
                .as_ref()
                .map(|path| path.to_string_lossy().into_owned()),
            "dependency_root": ready
                .package
                .dependency_root
                .as_ref()
                .map(|path| path.to_string_lossy().into_owned()),
            "package_published": ready.package.package_published,
            "setup_ran": ready.package.setup_ran,
        }),
    );
    if let Some(setup) = &ready.package.setup {
        record_plugin_setup_finished(context, setup);
    }
    for process in &ready.started_service_processes {
        context.record_trace_event(
            "plugin",
            "service_started",
            json!({
                "plugin": ready.plugin_id,
                "service_id": process.service_id,
                "service_instance_id": process.service_instance_id,
                "pid": process.pid,
                "process_group_id": process.process_group_id,
                "running": process.running,
                "socket_path": process.socket_path,
                "stderr_path": process.stderr_path,
            }),
        );
    }
}

pub(crate) fn record_plugin_ensure_error_trace_events(
    context: &DispatchContext<'_>,
    err: &PluginRuntimeError,
) {
    if let PluginRuntimeError::Ppc(PpcError::SetupFailed { report, .. }) = err {
        record_plugin_setup_finished(context, report);
    }
}

fn record_plugin_setup_finished(context: &DispatchContext<'_>, report: &PluginSetupReport) {
    context.record_trace_event(
        "plugin",
        "setup_finished",
        json!({
            "plugin": report.plugin,
            "digest": report.digest,
            "ran": report.ran,
            "success": report.success,
            "exit_code": report.exit_code,
            "output_tail": report.output_tail,
            "spawn_error": report.spawn_error,
        }),
    );
}

pub(crate) fn record_plugin_status_trace_events(
    context: &DispatchContext<'_>,
    outcome: &StatusOutcome,
) {
    for health in &outcome.service_health {
        context.record_trace_event(
            "plugin",
            "service_health_checked",
            json!({
                "plugin": health.plugin,
                "service_id": health.service_id,
                "service_instance_id": health.service_instance_id,
                "manifest_key": health.manifest_key,
                "state": health.state,
                "restart_count": health.restart_count,
                "refresh_count": health.refresh_count,
                "last_error": health.last_error,
                "accepted": health.accepted,
                "success": health.success,
                "error": health.error,
                "teardown_error": health.teardown_error,
            }),
        );
    }
    for process in &outcome.exited_service_processes {
        record_service_exited(context, process);
    }
    for process in outcome
        .running_service_processes
        .iter()
        .filter(|process| !process.running)
    {
        record_service_exited(context, process);
    }
}

fn record_service_exited(context: &DispatchContext<'_>, process: &ServiceProcessStatus) {
    context.record_trace_event(
        "plugin",
        "service_exited",
        json!({
            "service_id": process.service_id,
            "service_instance_id": process.service_instance_id,
            "pid": process.pid,
            "process_group_id": process.process_group_id,
            "exit_code": process.exit_status,
            "signal": process.exit_signal,
            "status_raw": process.status_raw,
            "socket_path": process.socket_path,
            "stderr_path": process.stderr_path,
        }),
    );
}
