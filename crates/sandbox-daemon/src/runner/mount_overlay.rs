use anyhow::Result;
use sandbox_config::configs::runner::RunnerConfig;

pub(crate) fn run(
    request: &sandbox_runtime_namespace_process::runner::protocol::NamespaceRunnerRequest,
    runner_config: &RunnerConfig,
) -> Result<sandbox_runtime_namespace_process::runner::protocol::RunResult> {
    Ok(mount_overlay_result(
        sandbox_runtime_namespace_process::runner::setns::setns_overlay_mount(
            request,
            &runner_config.mount_mask.hidden_paths,
        ),
    ))
}

pub(crate) fn mount_overlay_result(
    outcome: Result<(), impl std::fmt::Display>,
) -> sandbox_runtime_namespace_process::runner::protocol::RunResult {
    match outcome {
        Ok(()) => ok_result(),
        Err(error) => sandbox_runtime_namespace_process::runner::protocol::RunResult {
            exit_code: 1,
            payload: serde_json::json!({
                "error": format!("ns-runner setns overlay mount failed: {error}")
            }),
        },
    }
}

fn ok_result() -> sandbox_runtime_namespace_process::runner::protocol::RunResult {
    sandbox_runtime_namespace_process::runner::protocol::RunResult {
        exit_code: 0,
        payload: serde_json::json!({"success": true, "status": "ok"}),
    }
}
