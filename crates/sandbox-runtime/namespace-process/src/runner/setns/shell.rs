use crate::runner::protocol::{NamespaceRunnerRequest, RunResult};
use crate::runner::RunnerError;

pub(crate) fn run_setns(request: &NamespaceRunnerRequest) -> Result<RunResult, RunnerError> {
    let ns_fds = request
        .ns_fds
        .ok_or_else(|| RunnerError::InvalidRequest("setns mode requires ns_fds".to_owned()))?;
    super::namespaces::join_namespaces(&ns_fds)?;
    crate::runner::shell_exec::execute_shell(request)
}
