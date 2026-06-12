use eos_namespace::protocol::{Intent, RunMode, RunRequest, RunnerVerb, ToolCall, WorkspaceRoot};

use super::*;

#[test]
fn finish_prepare_records_prepared_and_metadata_artifact_events() {
    let root = std::env::temp_dir().join(format!(
        "eos-operation-command-prepare-{}",
        std::process::id()
    ));
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
                intent: Intent::WriteAllowed,
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
