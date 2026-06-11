use crate::core::ops::{BuiltinOp, OpContract};

pub const FAMILY_OPS: &[BuiltinOp] = &[
    BuiltinOp::IsolatedWorkspaceEnter,
    BuiltinOp::IsolatedWorkspaceExit,
    BuiltinOp::IsolatedWorkspaceStatus,
    BuiltinOp::IsolatedWorkspaceListOpen,
    BuiltinOp::IsolatedWorkspaceTestReset,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum IsolationOp {
    Enter,
    Exit,
    Status,
    ListOpen,
    TestReset,
}

impl IsolationOp {
    #[must_use]
    pub fn contract(self) -> &'static OpContract {
        match self {
            Self::Enter => BuiltinOp::IsolatedWorkspaceEnter,
            Self::Exit => BuiltinOp::IsolatedWorkspaceExit,
            Self::Status => BuiltinOp::IsolatedWorkspaceStatus,
            Self::ListOpen => BuiltinOp::IsolatedWorkspaceListOpen,
            Self::TestReset => BuiltinOp::IsolatedWorkspaceTestReset,
        }
        .contract()
    }
}
