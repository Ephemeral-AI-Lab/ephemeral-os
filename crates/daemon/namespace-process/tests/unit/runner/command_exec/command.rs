use super::{normalize_lexical, shell_argv, shell_cwd};
use crate::runner::protocol::{NamespaceCommandRequest, WorkspaceRoot};
use std::path::Path;

type TestResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn exec_command_string_uses_non_login_bash() -> TestResult {
    let argv = shell_argv(&request(serde_json::json!({"command": "echo hi"})))?;
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
    let error = match shell_argv(&request(serde_json::json!({"command": ["echo", "hi"]}))) {
        Ok(argv) => {
            return Err(format!("exec_command raw argv unexpectedly accepted: {argv:?}").into())
        }
        Err(error) => error,
    };
    assert!(error.to_string().contains("shell-format command string"));
    Ok(())
}

#[test]
fn exec_command_rejects_external_cwd_unless_remountable() -> TestResult {
    let external = format!("/tmp/namespace-remountable-cwd-{}", std::process::id());
    let rejected = request(serde_json::json!({"command": "pwd", "cwd": external}));
    let error = shell_cwd(&rejected).expect_err("external cwd should require remountable opt-in");
    assert!(error.to_string().contains("cwd escapes workspace"));
    Ok(())
}

#[test]
fn exec_command_remountable_allows_external_cwd() -> TestResult {
    let external =
        std::env::temp_dir().join(format!("namespace-remountable-cwd-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&external);
    let allowed = request(serde_json::json!({
        "command": "pwd",
        "cwd": external,
        "remountable": true,
    }));
    assert_eq!(shell_cwd(&allowed)?, external);
    let _ = std::fs::remove_dir_all(external);
    Ok(())
}

#[test]
fn normalizes_paths_without_touching_fs() {
    assert_eq!(
        normalize_lexical(Path::new("/workspace/./a/../b")),
        Path::new("/workspace/b")
    );
}

fn request(args: serde_json::Value) -> NamespaceCommandRequest {
    NamespaceCommandRequest {
        invocation_id: "test".to_owned(),
        caller_id: "caller".to_owned(),
        args,
        workspace_root: WorkspaceRoot(Path::new("/workspace").to_path_buf()),
        layer_paths: vec![],
        upperdir: None,
        workdir: None,
        ns_fds: None,
        cgroup_path: None,
        timeout_seconds: None,
    }
}
