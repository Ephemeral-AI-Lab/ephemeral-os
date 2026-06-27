use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::json;

use crate::runner::protocol::NamespaceRunnerRequest;

#[test]
fn command_timeout_survives_as_timed_out_result() {
    let workspace_root = unique_workspace_root();
    std::fs::create_dir_all(&workspace_root).expect("create workspace root");
    let request = NamespaceRunnerRequest {
        request_id: "timeout-regression".to_owned(),
        args: json!({ "command": "sleep 5", "cwd": "." }),
        workspace_root: workspace_root.clone(),
        layer_paths: vec![],
        upperdir: None,
        workdir: None,
        ns_fds: None,
        timeout_seconds: Some(0.05),
    };

    let result =
        crate::runner::shell_exec::execute_shell(&request).expect("timeout should produce a result");

    assert_eq!(result.exit_code, 124);
    assert_eq!(result.payload["status"], "timed_out");
    let _ = std::fs::remove_dir_all(workspace_root);
}

fn unique_workspace_root() -> std::path::PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock after epoch")
        .as_nanos();
    std::env::temp_dir().join(format!(
        "eos-namespace-process-shell-timeout-{}-{nanos}",
        std::process::id()
    ))
}
