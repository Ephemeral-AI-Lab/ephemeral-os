use anyhow::{Context, Result};

pub(crate) fn run(
    request: &sandbox_runtime_namespace_process::runner::protocol::NamespaceRunnerRequest,
) -> Result<sandbox_runtime_namespace_process::runner::protocol::RunResult> {
    sandbox_runtime_namespace_process::runner::run(request).context("ns-runner failed")
}
