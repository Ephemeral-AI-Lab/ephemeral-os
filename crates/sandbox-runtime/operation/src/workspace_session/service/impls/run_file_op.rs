use crate::workspace_crate::{
    decode_file_op_payload, FileRunnerError, FileRunnerOp, FileRunnerResult, WorkspaceError,
};
use std::sync::PoisonError;

use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::model::WorkspaceSessionHandler;

impl WorkspaceSessionService {
    /// Run a file operation inside the resolved session's live namespace (peer of
    /// [`Self::capture_session_changes`]). Derives the session `cgroup.procs`
    /// placement, delegates to the workspace runtime, and decodes the runner
    /// result envelope into the file-op outcome. Session file ops never publish.
    ///
    /// # Errors
    /// Returns [`WorkspaceSessionError`] when the runtime launch fails or the
    /// runner returns no valid result envelope. A file-op-level failure
    /// (not-regular, not-UTF-8, …) is the inner `Err(FileRunnerError)`.
    pub fn run_file_op(
        &self,
        handler: &WorkspaceSessionHandler,
        op: FileRunnerOp,
    ) -> Result<Result<FileRunnerResult, FileRunnerError>, WorkspaceSessionError> {
        let gate = self.session_gate(&handler.workspace_session_id);
        let _admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
        let cgroup_procs_path = handler
            .cgroup_path
            .as_ref()
            .map(|cgroup_path| cgroup_path.join("cgroup.procs"));
        let result = self
            .workspace()
            .run_file_op(&handler.handle, cgroup_procs_path, op)?;
        decode_file_op_payload(&result.payload).ok_or_else(|| {
            WorkspaceSessionError::Workspace(WorkspaceError::Command {
                message: "namespace file runner returned no result envelope".to_owned(),
            })
        })
    }
}
