use crate::core::catalog::{BuiltinOp, OpContract};

pub const FAMILY_OPS: &[BuiltinOp] = &[
    BuiltinOp::LayerMetrics,
    BuiltinOp::EnsureWorkspaceBase,
    BuiltinOp::BuildWorkspaceBase,
    BuiltinOp::CommitToWorkspace,
    BuiltinOp::CommitToGit,
    BuiltinOp::WorkspaceBinding,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CheckpointOp {
    LayerMetrics,
    EnsureWorkspaceBase,
    BuildWorkspaceBase,
    CommitToWorkspace,
    CommitToGit,
    WorkspaceBinding,
}

impl CheckpointOp {
    #[must_use]
    pub fn contract(self) -> &'static OpContract {
        match self {
            Self::LayerMetrics => BuiltinOp::LayerMetrics,
            Self::EnsureWorkspaceBase => BuiltinOp::EnsureWorkspaceBase,
            Self::BuildWorkspaceBase => BuiltinOp::BuildWorkspaceBase,
            Self::CommitToWorkspace => BuiltinOp::CommitToWorkspace,
            Self::CommitToGit => BuiltinOp::CommitToGit,
            Self::WorkspaceBinding => BuiltinOp::WorkspaceBinding,
        }
        .contract()
    }
}
