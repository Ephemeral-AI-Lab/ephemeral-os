use super::{plugin_service_argv, shell_argv};
use crate::protocol::Intent;
use crate::protocol::{RunMode, RunRequest, RunnerVerb, ToolCall, WorkspaceRoot};
use crate::runner::path::normalize_lexical;
use std::path::Path;

type TestResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn exec_command_string_uses_non_login_bash() -> TestResult {
    let argv = shell_argv(&request(
        "exec_command",
        serde_json::json!({"command": "echo hi"}),
    ))?;
    assert_eq!(
        argv,
        ["/bin/bash", "--noprofile", "--norc", "-c", "echo hi"]
            .map(str::to_owned)
            .to_vec()
    );
    Ok(())
}

#[test]
fn exec_command_rejects_raw_argv() -> TestResult {
    let error = match shell_argv(&request(
        "exec_command",
        serde_json::json!({"command": ["echo", "hi"]}),
    )) {
        Ok(argv) => {
            return Err(format!("exec_command raw argv unexpectedly accepted: {argv:?}").into())
        }
        Err(error) => error,
    };
    assert!(error.to_string().contains("shell-format command string"));
    Ok(())
}

#[test]
fn plugin_service_requires_argv_command() -> TestResult {
    let argv = plugin_service_argv(&request(
        "plugin_service",
        serde_json::json!({"command": ["python3", "/eos/plugin/harness.py"]}),
    ))?;
    assert_eq!(
        argv,
        ["python3", "/eos/plugin/harness.py"]
            .map(str::to_owned)
            .to_vec()
    );

    let error = match plugin_service_argv(&request(
        "plugin_service",
        serde_json::json!({"command": "python3 /eos/plugin/harness.py"}),
    )) {
        Ok(argv) => {
            return Err(
                format!("plugin_service string command unexpectedly accepted: {argv:?}").into(),
            );
        }
        Err(error) => error,
    };
    assert!(error.to_string().contains("argv list"));
    Ok(())
}

#[test]
fn normalizes_paths_without_touching_fs() {
    assert_eq!(
        normalize_lexical(Path::new("/workspace/./a/../b")),
        Path::new("/workspace/b")
    );
}

fn request(verb: &str, args: serde_json::Value) -> RunRequest {
    RunRequest {
        mode: RunMode::FreshNs,
        tool_call: ToolCall {
            invocation_id: "test".to_owned(),
            caller_id: "caller".to_owned(),
            verb: RunnerVerb::from(verb),
            intent: Intent::WriteAllowed,
            args,
            background: false,
        },
        workspace_root: WorkspaceRoot(Path::new("/workspace").to_path_buf()),
        layer_paths: vec![],
        upperdir: None,
        workdir: None,
        ns_fds: None,
        cgroup_path: None,
        timeout_seconds: None,
    }
}
