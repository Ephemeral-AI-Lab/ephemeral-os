use linux_namespace_subprocess::protocol::{
    RunMode, RunRequest, RunnerVerb, ToolCall, WorkspaceRoot,
};

use super::*;

#[test]
fn finish_prepare_records_prepared_and_metadata_artifact_events() {
    let root =
        std::env::temp_dir().join(format!("operation-command-prepare-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let command_dir = root.join("command");
    let request_path = root.join("runner-request.json");
    let output_path = root.join("runner-result.json");

    let prepared = finish_prepare(
        PrepareInputs {
            caller_id: "caller",
            command_id: "cmd_prepare",
            invocation_id: "invoke",
            cmd: "echo ok",
            cwd: None,
            remountable: false,
            timeout_seconds: Some(5.0),
            command_dir: command_dir.clone(),
            workspace_label: "isolated",
        },
        RunRequest {
            mode: RunMode::FreshNs,
            tool_call: ToolCall {
                invocation_id: "invoke".to_owned(),
                caller_id: "caller".to_owned(),
                verb: RunnerVerb::ExecCommand,
                args: serde_json::json!({"command": "echo ok"}),
                background: false,
            },
            workspace_root: WorkspaceRoot(root.join("workspace")),
            layer_paths: Vec::new(),
            upperdir: None,
            workdir: None,
            ns_fds: None,
            cgroup_path: None,
            timeout_seconds: Some(5.0),
        },
        request_path,
        output_path,
    )
    .expect("prepare command");

    assert_eq!(prepared.trace_events.len(), 2);
    assert_eq!(prepared.trace_events[0].name, "prepared");
    assert_eq!(
        prepared.trace_events[0].details["command_id"],
        "cmd_prepare"
    );
    assert_eq!(prepared.trace_events[0].details["workspace"], "isolated");
    assert_eq!(prepared.trace_events[1].name, "artifact_written");
    assert_eq!(prepared.trace_events[1].details["artifact"], "metadata");
    assert_eq!(
        prepared.trace_events[1].details["path"],
        command_dir.join("metadata.json").display().to_string()
    );
    assert!(
        prepared.trace_events[1].details["bytes"]
            .as_u64()
            .expect("metadata byte count")
            > 0
    );

    let metadata =
        std::fs::read_to_string(command_dir.join("metadata.json")).expect("metadata written");
    assert!(metadata.contains("\"command_id\": \"cmd_prepare\""));

    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn finish_prepare_reports_metadata_artifact_write_failure() {
    let root = std::env::temp_dir().join(format!(
        "operation-command-prepare-failure-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let command_dir = root.join("command");
    std::fs::create_dir_all(command_dir.join("metadata.json"))
        .expect("create obstructing metadata directory");

    let error = match finish_prepare(
        PrepareInputs {
            caller_id: "caller",
            command_id: "cmd_prepare",
            invocation_id: "invoke",
            cmd: "echo ok",
            cwd: None,
            remountable: false,
            timeout_seconds: Some(5.0),
            command_dir: command_dir.clone(),
            workspace_label: "isolated",
        },
        RunRequest {
            mode: RunMode::FreshNs,
            tool_call: ToolCall {
                invocation_id: "invoke".to_owned(),
                caller_id: "caller".to_owned(),
                verb: RunnerVerb::ExecCommand,
                args: serde_json::json!({"command": "echo ok"}),
                background: false,
            },
            workspace_root: WorkspaceRoot(root.join("workspace")),
            layer_paths: Vec::new(),
            upperdir: None,
            workdir: None,
            ns_fds: None,
            cgroup_path: None,
            timeout_seconds: Some(5.0),
        },
        root.join("runner-request.json"),
        root.join("runner-result.json"),
    ) {
        Ok(_) => panic!("metadata path is a directory"),
        Err(error) => error,
    };

    assert_eq!(error.error.kind, "command_prepare_failed");
    assert_eq!(error.trace_events.len(), 1);
    let event = error.trace_events.first().expect("artifact failed event");
    assert_eq!(event.name, "artifact_failed");
    assert_eq!(event.details["artifact"], "metadata");
    assert_eq!(
        event.details["path"],
        command_dir.join("metadata.json").display().to_string()
    );
    assert!(event.details["error"]
        .as_str()
        .is_some_and(|error| !error.is_empty()));

    let _ = std::fs::remove_dir_all(root);
}
