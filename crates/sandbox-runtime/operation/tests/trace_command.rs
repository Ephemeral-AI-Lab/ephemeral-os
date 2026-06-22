mod support;

use std::path::PathBuf;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use sandbox_protocol::{CliOperationScope, Request};
use sandbox_runtime::command::{ExecCommandInput, ReadCommandLinesInput, WriteCommandStdinInput};
use sandbox_runtime_command::yield_wait_loop::WaitOutcome;
use sandbox_runtime_workspace::WorkspaceProfile;
use serde_json::json;

use support::{
    build_services_with_launch_driver, create_request, success_exit, trace::capture_traces,
    workspace_handle, FakeLaunchDriver, FakeWorkspaceService,
};

#[test]
fn command_trace_spans_omit_sensitive_values() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    launch_driver.push_outcome(WaitOutcome::Running(
        "STDOUT_SECRET_SENTINEL initial\n".to_owned(),
    ));
    launch_driver.push_outcome(WaitOutcome::Completed(success_exit(
        "STDERR_STDOUT_SECRET_SENTINEL final\n",
    )));
    let env = build_services_with_launch_driver(Arc::clone(&fake), launch_driver);
    fake.push_create_result(Ok(workspace_handle(
        "workspace-secret",
        "lease-secret",
        PathBuf::from("/workspace/PATH_SECRET_SENTINEL"),
        WorkspaceProfile::HostCompatible,
    )));
    let workspace_session_id = env
        .workspace
        .create_workspace_session(create_request())
        .expect("session create succeeds")
        .workspace_session_id;

    let traces = capture_traces(|| {
        let command_session_id = env
            .command
            .exec_command(ExecCommandInput {
                workspace_session_id: Some(workspace_session_id),
                cmd: "printf COMMAND_SECRET_SENTINEL && export TOKEN=AUTH_ENV_SECRET_SENTINEL"
                    .to_owned(),
                timeout_ms: Some(2500),
                yield_time_ms: Some(1),
            })
            .expect("command starts")
            .command_session_id
            .expect("running command id returned");

        env.command
            .write_command_stdin(WriteCommandStdinInput {
                command_session_id: command_session_id.clone(),
                stdin: "STDIN_SECRET_SENTINEL\n".to_owned(),
                yield_time_ms: Some(1),
            })
            .expect("stdin write completes command");

        env.command
            .read_command_lines(ReadCommandLinesInput {
                command_session_id,
                start_offset: Some(0),
                limit: Some(10),
            })
            .expect("completed command output remains readable");
    });

    for expected in [
        "runtime.exec_command",
        "command.spawn",
        "command.wait_initial_yield",
        "runtime.write_command_stdin",
        "command.finalize",
        "runtime.read_command_lines",
    ] {
        assert!(traces.contains(expected), "missing {expected} in {traces}");
    }
    for forbidden in [
        "COMMAND_SECRET_SENTINEL",
        "AUTH_ENV_SECRET_SENTINEL",
        "STDIN_SECRET_SENTINEL",
        "STDOUT_SECRET_SENTINEL",
        "STDERR_STDOUT_SECRET_SENTINEL",
        "PATH_SECRET_SENTINEL",
        "/workspace/",
        "/lower/one",
        "transcript.log",
        "lease-secret",
    ] {
        assert!(
            !traces.contains(forbidden),
            "forbidden value {forbidden} appeared in traces: {traces}"
        );
    }
}

#[test]
fn public_cgroup_read_operations_emit_no_trace_spans() {
    let operations = test_operations();

    let traces = capture_traces(|| {
        let inspect = Request::new(
            "inspect_cgroup_monitor",
            "req-inspect",
            CliOperationScope::sandbox("scope-sbox"),
            json!({
                "workspace_session_id": "workspace-secret",
                "command_session_id": "command-secret",
            }),
        );
        let read = Request::new(
            "read_cgroup_monitor_samples",
            "req-read",
            CliOperationScope::sandbox("scope-sbox"),
            json!({
                "workspace_session_id": "workspace-secret",
                "command_session_id": "command-secret",
                "limit": 5,
            }),
        );

        let _ = sandbox_runtime::dispatch_operation(&operations, &inspect);
        let _ = sandbox_runtime::dispatch_operation(&operations, &read);
    });

    assert!(
        traces.trim().is_empty(),
        "public cgroup read ops must not emit trace spans/events: {traces}"
    );
}

fn test_operations() -> sandbox_runtime::SandboxRuntimeOperations {
    let base = temp_root("trace-cgroup");
    let workspace_root = base.join("workspace");
    let layer_stack_root = base.join("layer-stack");
    std::fs::create_dir_all(&workspace_root).expect("create trace cgroup workspace");
    sandbox_runtime_layerstack::build_workspace_base(&layer_stack_root, &workspace_root, false)
        .expect("build trace cgroup layerstack workspace base");

    sandbox_runtime::SandboxRuntimeOperations::from_config(sandbox_runtime::SandboxRuntimeConfig {
        workspace: sandbox_runtime::WorkspaceRuntimeConfig {
            workspace_root,
            layer_stack_root,
            scratch_root: base.join("workspace-scratch"),
            caps: sandbox_runtime::WorkspaceResourceCaps {
                ttl_s: 60.0,
                total_cap: 2,
                upperdir_bytes: 1024 * 1024,
                memavail_fraction: 0.5,
                setup_timeout_s: 1.0,
                exit_grace_s: 0.1,
                rfc1918_egress: sandbox_runtime::Rfc1918Egress::Allow,
            },
        },
        command: sandbox_runtime::CommandRuntimeConfig {
            scratch_root: base.join("command-scratch"),
        },
        cgroup_monitor: sandbox_runtime::CgroupMonitorRuntimeConfig {
            enabled: false,
            sample_interval_ms: 1000,
            retained_samples_per_target: 10,
            include_pids: false,
            include_pressure: false,
            include_disk: false,
        },
    })
}

fn temp_root(label: &str) -> PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system time after epoch")
        .as_nanos();
    std::env::temp_dir().join(format!("{label}-{}-{nanos}", std::process::id()))
}
