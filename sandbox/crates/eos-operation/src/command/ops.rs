use crate::core::catalog::{BuiltinOp, OpContract};

pub const FAMILY_OPS: &[BuiltinOp] = &[
    BuiltinOp::ExecCommand,
    BuiltinOp::WriteStdin,
    BuiltinOp::CommandReadProgress,
    BuiltinOp::CommandCancel,
    BuiltinOp::CommandCollectCompleted,
    BuiltinOp::CommandSessionCount,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CommandOp {
    Exec,
    WriteStdin,
    Poll,
    Cancel,
    CollectCompleted,
    Count,
}

impl CommandOp {
    #[must_use]
    pub fn contract(self) -> &'static OpContract {
        match self {
            Self::Exec => BuiltinOp::ExecCommand,
            Self::WriteStdin => BuiltinOp::WriteStdin,
            Self::Poll => BuiltinOp::CommandReadProgress,
            Self::Cancel => BuiltinOp::CommandCancel,
            Self::CollectCompleted => BuiltinOp::CommandCollectCompleted,
            Self::Count => BuiltinOp::CommandSessionCount,
        }
        .contract()
    }
}
