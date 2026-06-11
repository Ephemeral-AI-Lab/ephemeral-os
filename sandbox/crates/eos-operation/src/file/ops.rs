use crate::core::catalog::{BuiltinOp, OpContract};

pub const FAMILY_OPS: &[BuiltinOp] = &[
    BuiltinOp::ReadFile,
    BuiltinOp::WriteFile,
    BuiltinOp::EditFile,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FileOp {
    Read,
    Write,
    Edit,
}

impl FileOp {
    #[must_use]
    pub fn contract(self) -> &'static OpContract {
        match self {
            Self::Read => BuiltinOp::ReadFile,
            Self::Write => BuiltinOp::WriteFile,
            Self::Edit => BuiltinOp::EditFile,
        }
        .contract()
    }
}
