//! Adapter-side isolated-workspace op tests. Lifecycle behavior lives in
//! `workspace_runtime` unit tests; dispatch-level coverage lives in the
//! daemon's `phase2_read_paths` integration tests.

use std::collections::HashMap;

use eos_workspace::{DnsConfiguration, IsolatedWorkspaceId, OverlayDirs};

use super::*;

#[test]
fn host_ram_pressure_error_keeps_capacity_details() {
    let response = error_payload(&IsolatedError::HostRamPressure {
        required_bytes: 30,
        budget_bytes: 29,
    })
    .into_wire();
    assert_eq!(response["success"], false);
    assert_eq!(response["error"]["kind"], "host_ram_pressure");
    assert_eq!(response["error"]["details"]["required_bytes"], 30);
    assert_eq!(response["error"]["details"]["budget_bytes"], 29);
}

#[test]
fn enter_trace_events_include_holder_and_dns_configuration() {
    let sink = crate::trace::RequestTraceEventSink::default();
    let context = DispatchContext::empty().with_trace_events(sink.clone());
    let handle = test_handle();

    record_enter_started(
        &context,
        "caller-isolated",
        std::path::Path::new("/tmp/layer-stack"),
    );
    record_entered(&context, &handle);

    let events = sink.drain();
    assert_eq!(events.len(), 3);
    assert_eq!(events[0].module, "isolated_workspace");
    assert_eq!(events[0].name, "enter_started");
    assert_eq!(events[0].details["caller_id"], "caller-isolated");
    assert_eq!(events[0].details["layer_stack_root"], "/tmp/layer-stack");
    assert_eq!(events[1].name, "holder_started");
    assert_eq!(events[1].details["workspace_handle_id"], "workspace-handle");
    assert_eq!(events[1].details["holder_pid"], 42);
    assert_eq!(events[2].name, "network_configured");
    assert_eq!(events[2].details["dns_fallback_applied"], true);
    assert_eq!(events[2].details["previous_first_nameserver"], "127.0.0.53");
}

#[test]
fn status_trace_event_records_open_closed_and_error_states() {
    let sink = crate::trace::RequestTraceEventSink::default();
    let context = DispatchContext::empty().with_trace_events(sink.clone());
    let handle = test_handle();

    record_status_read(&context, "caller-isolated", Some(&handle), None);
    record_status_read(&context, "caller-closed", None, None);
    record_status_read(&context, "caller-error", None, Some("not_open"));

    let events = sink.drain();
    assert_eq!(events.len(), 3);
    assert_eq!(events[0].name, "status_read");
    assert_eq!(events[0].details["open"], true);
    assert_eq!(events[0].details["workspace_handle_id"], "workspace-handle");
    assert_eq!(events[1].details["open"], false);
    assert!(events[1].details["error_kind"].is_null());
    assert_eq!(events[2].details["error_kind"], "not_open");
}

#[test]
fn exit_trace_events_include_teardown_phases_and_mountinfo_marker() {
    let sink = crate::trace::RequestTraceEventSink::default();
    let context = DispatchContext::empty().with_trace_events(sink.clone());
    let mut phases = HashMap::new();
    phases.insert("kill_holder".to_owned(), 1.5);
    phases.insert("rmtree_scratch".to_owned(), 2.5);
    let exit = ExitOutcome {
        isolated: eos_workspace::ExitOutcome {
            workspace_id: IsolatedWorkspaceId("workspace-handle".to_owned()),
            caller_id: "caller-isolated".to_owned(),
            lease_id: "lease-1".to_owned(),
            evicted_upperdir_bytes: 4096,
            lifetime_s: 12.0,
            total_ms: 4.0,
            phases_ms: phases,
            inspection: json!({
                "holder_pid": 42,
                "holder_kill_error": null,
                "mountinfo_reference_count_after": null,
            }),
        },
        lease_released: Some(true),
        active_leases_after: 0,
    };

    record_exit_started(&context, "caller-isolated");
    record_exited(&context, &exit);

    let events = sink.drain();
    assert_eq!(events.len(), 4);
    assert_eq!(events[0].name, "exit_started");
    assert_eq!(events[1].name, "teardown_phase_finished");
    assert_eq!(events[1].details["phase"], "kill_holder");
    assert_eq!(events[1].details["holder_was_alive"], true);
    assert_eq!(events[2].details["phase"], "rmtree_scratch");
    assert_eq!(events[3].name, "exited");
    assert_eq!(events[3].details["workspace_handle_id"], "workspace-handle");
    assert_eq!(events[3].details["lease_released"], true);
    assert_eq!(events[3].details["mountinfo_scan_error"], true);
}

#[test]
fn recovery_trace_events_include_manager_json_and_cleanup_errors() {
    let sink = crate::trace::RequestTraceEventSink::default();
    let context = DispatchContext::empty().with_trace_events(sink.clone());
    let recovery = WorkspaceRecoveryReport {
        exited_callers: vec!["caller-isolated".to_owned()],
        manager_json_error: Some(
            "manager_json_schema: expected schema_version 1, got 999".to_owned(),
        ),
        orphan_cleanup_error: Some("scratch cleanup failed".to_owned()),
    };

    record_recovery_started(&context);
    record_recovery_finished(&context, &recovery);

    let events = sink.drain();
    assert_eq!(events.len(), 2);
    assert_eq!(events[0].module, "isolated_workspace");
    assert_eq!(events[0].name, "recovery_started");
    assert_eq!(events[1].name, "recovery_finished");
    assert_eq!(events[1].details["exited_caller_count"], 1);
    assert_eq!(
        events[1].details["exited_callers"],
        json!(["caller-isolated"])
    );
    assert_eq!(
        events[1].details["manager_json_error"],
        "manager_json_schema: expected schema_version 1, got 999"
    );
    assert_eq!(
        events[1].details["orphan_cleanup_error"],
        "scratch cleanup failed"
    );
}

fn test_handle() -> WorkspaceHandle {
    WorkspaceHandle {
        workspace_id: IsolatedWorkspaceId("workspace-handle".to_owned()),
        caller_id: "caller-isolated".to_owned(),
        lease_id: "lease-1".to_owned(),
        manifest_version: 7,
        manifest_root_hash: "root".to_owned(),
        workspace_root: "/workspace".to_owned(),
        dirs: OverlayDirs {
            run_dir: "/tmp/run".into(),
            upperdir: "/tmp/upper".into(),
            workdir: "/tmp/work".into(),
        },
        layer_paths: Vec::new(),
        ns_fds: HashMap::new(),
        holder_pid: 42,
        readiness_fd: -1,
        control_fd: -1,
        veth: None,
        cgroup_path: None,
        dns_configuration: DnsConfiguration {
            fallback_applied: true,
            previous_first_nameserver: Some("127.0.0.53".to_owned()),
        },
        created_at: 1.0,
        last_activity: 2.0,
    }
}
