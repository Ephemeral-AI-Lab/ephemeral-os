use anyhow::Result;

/// `--file-op` runner body (peer of `mount_overlay::run`): `setns` into the
/// session namespaces and run one file operation, encoding the outcome as a
/// [`RunResult`]. File-op outcomes use exit code 0; the launcher inspects the
/// result payload, not the exit code.
pub(crate) fn run(
    request: &sandbox_runtime_namespace_process::runner::protocol::NamespaceRunnerRequest,
) -> Result<sandbox_runtime_namespace_process::runner::protocol::RunResult> {
    Ok(sandbox_runtime_namespace_process::runner::file_op::run_file_op(request))
}
