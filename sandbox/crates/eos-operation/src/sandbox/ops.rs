use crate::core::ops::{BuiltinOp, OpContract};

pub const FAMILY_OPS: &[BuiltinOp] = &[
    BuiltinOp::SandboxAcquire,
    BuiltinOp::SandboxRelease,
    BuiltinOp::SandboxStatus,
    BuiltinOp::SandboxList,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SandboxOp {
    Acquire,
    Release,
    Status,
    List,
}

impl SandboxOp {
    #[must_use]
    pub fn contract(self) -> &'static OpContract {
        match self {
            Self::Acquire => BuiltinOp::SandboxAcquire,
            Self::Release => BuiltinOp::SandboxRelease,
            Self::Status => BuiltinOp::SandboxStatus,
            Self::List => BuiltinOp::SandboxList,
        }
        .contract()
    }
}
