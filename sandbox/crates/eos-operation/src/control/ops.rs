use crate::core::catalog::{BuiltinOp, OpContract};

pub const FAMILY_OPS: &[BuiltinOp] = &[
    BuiltinOp::RuntimeReady,
    BuiltinOp::InvocationHeartbeat,
    BuiltinOp::InvocationCancel,
    BuiltinOp::InflightCount,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ControlOp {
    RuntimeReady,
    InvocationHeartbeat,
    InvocationCancel,
    InflightCount,
}

impl ControlOp {
    #[must_use]
    pub fn contract(self) -> &'static OpContract {
        match self {
            Self::RuntimeReady => BuiltinOp::RuntimeReady,
            Self::InvocationHeartbeat => BuiltinOp::InvocationHeartbeat,
            Self::InvocationCancel => BuiltinOp::InvocationCancel,
            Self::InflightCount => BuiltinOp::InflightCount,
        }
        .contract()
    }
}
