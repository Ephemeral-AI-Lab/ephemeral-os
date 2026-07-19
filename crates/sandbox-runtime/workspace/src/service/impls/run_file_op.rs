use std::path::PathBuf;

use sandbox_runtime_namespace_process::runner::file_op::FileRunnerOp;
use sandbox_runtime_namespace_process::runner::protocol::RunResult;
use serde_json::Value;

use crate::error::WorkspaceError;
use crate::model::WorkspaceHandle;
use crate::service::support::workspace_error_from_manager_error;
use crate::service::WorkspaceRuntimeService;

impl WorkspaceRuntimeService {
    /// Run a file operation against a live session's mounted workspace through the
    /// namespace runner (peer of [`Self::capture_changes`]). Returns the raw
    /// runner [`RunResult`]; the caller decodes the file-op payload. Never mounts
    /// and never mutates the host `upperdir`.
    ///
    /// # Errors
    /// Returns [`WorkspaceError`] when the session is not open or the runner
    /// launch fails.
    pub fn run_file_op(
        &self,
        handle: &WorkspaceHandle,
        cgroup_procs_path: Option<PathBuf>,
        op: FileRunnerOp,
    ) -> Result<RunResult, WorkspaceError> {
        let _admission = self.admit_work()?;
        if let Some(hooks) = self.hooks() {
            return (hooks.run_file_op)(handle, op);
        }
        let args: Value = serde_json::to_value(&op).map_err(|error| WorkspaceError::Setup {
            step: format!("serialize file op: {error}"),
        })?;
        let state = self.lock_state()?;
        let session = state
            .manager
            .handles
            .get(&handle.id)
            .ok_or(WorkspaceError::NotOpen)?;
        if !handle.matches_mounted_workspace(session) || !handle.holder_is_live() {
            return Err(WorkspaceError::NotOpen);
        }
        state
            .manager
            .runtime
            .run_file_op(session, cgroup_procs_path, args)
            .map_err(workspace_error_from_manager_error)
    }
}
