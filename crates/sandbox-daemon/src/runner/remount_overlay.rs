use anyhow::Result;
use sandbox_config::configs::runner::RunnerConfig;

/// `--remount-overlay` runner body (peer of `mount_overlay::run`): `setns`
/// into the session's user+mount namespaces and run the staged switch; the
/// two-boolean report is the payload on every post-`setns` path.
pub(crate) fn run(
    request: &sandbox_runtime_namespace_process::runner::protocol::NamespaceRunnerRequest,
    runner_config: &RunnerConfig,
) -> Result<sandbox_runtime_namespace_process::runner::protocol::RunResult> {
    match sandbox_runtime_namespace_process::runner::setns::setns_remount_overlay(
        request,
        &runner_config.mount_mask.hidden_paths,
    ) {
        Ok(result) => Ok(result),
        Err(error) => Ok(
            sandbox_runtime_namespace_process::runner::protocol::RunResult {
                exit_code: 1,
                payload: serde_json::json!({
                    "error": format!("ns-runner setns remount overlay failed: {error}")
                }),
            },
        ),
    }
}
