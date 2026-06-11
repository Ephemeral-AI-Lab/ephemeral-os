use crate::core::ops::{BuiltinOp, OpContract};

pub const FAMILY_OPS: &[BuiltinOp] = &[
    BuiltinOp::CancelWorkspaceRunsByCaller,
    BuiltinOp::CancelWorkspaceRuns,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum WorkspaceRunOp {
    End,
    CancelAll,
}

impl WorkspaceRunOp {
    #[must_use]
    pub fn contract(self) -> &'static OpContract {
        match self {
            Self::End => BuiltinOp::CancelWorkspaceRunsByCaller,
            Self::CancelAll => BuiltinOp::CancelWorkspaceRuns,
        }
        .contract()
    }
}
