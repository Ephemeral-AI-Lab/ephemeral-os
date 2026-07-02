use std::collections::BTreeSet;
use std::path::PathBuf;

use sandbox_runtime_namespace_execution::NamespaceExecutionId;

use crate::workspace_crate::{NetworkProfile, WorkspaceHandle, WorkspaceSessionId};

/// What happens when a command completion empties the session's command
/// ledger. Fixed at creation; sessions created through the CLI are always
/// `NoOp`, `PublishThenDestroy` is set only by `exec_command`'s implicit
/// create.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FinalizePolicy {
    PublishThenDestroy,
    NoOp,
}

impl FinalizePolicy {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::PublishThenDestroy => "publish_then_destroy",
            Self::NoOp => "no_op",
        }
    }
}

/// Operation-layer session create request. Maps down to the policy-free
/// workspace-crate `CreateWorkspaceRequest`; the finalize policy stays in this
/// crate.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CreateSessionRequest {
    pub network: NetworkProfile,
    pub finalize_policy: FinalizePolicy,
}

/// Publish outcome of a finalize run, surfaced on the completing command's
/// terminal response through a once-set slot stored at attach (§2.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FinalizeOutcome {
    pub publish_reject_class: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceSessionHandler {
    pub workspace_session_id: WorkspaceSessionId,
    pub handle: WorkspaceHandle,
    pub cgroup_path: Option<PathBuf>,
}

/// Lifecycle phase of a session's finalization. `FinalizeFailed` and a session
/// stuck in `Finalizing` are destroyable through `guarded_destroy` only.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum FinalizationState {
    Active,
    Finalizing,
    FinalizeFailed,
}

#[derive(Debug, Clone)]
pub(crate) struct WorkspaceSession {
    pub workspace_session_id: WorkspaceSessionId,
    pub handle: WorkspaceHandle,
    pub cgroup_path: Option<PathBuf>,
    pub finalize_policy: FinalizePolicy,
    pub active_commands: BTreeSet<NamespaceExecutionId>,
    pub finalization_state: FinalizationState,
}

impl WorkspaceSession {
    pub(crate) fn from_handle(
        handle: WorkspaceHandle,
        cgroup_path: Option<PathBuf>,
        finalize_policy: FinalizePolicy,
    ) -> Self {
        Self {
            workspace_session_id: handle.id.clone(),
            handle,
            cgroup_path,
            finalize_policy,
            active_commands: BTreeSet::new(),
            finalization_state: FinalizationState::Active,
        }
    }

    pub(crate) fn handler(&self) -> WorkspaceSessionHandler {
        WorkspaceSessionHandler {
            workspace_session_id: self.workspace_session_id.clone(),
            handle: self.handle.clone(),
            cgroup_path: self.cgroup_path.clone(),
        }
    }
}
