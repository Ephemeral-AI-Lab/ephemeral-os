use crate::core::ops::{BuiltinOp, OpContract};

pub const FAMILY_OPS: &[BuiltinOp] = &[BuiltinOp::PluginEnsure, BuiltinOp::PluginStatus];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PluginOp {
    Ensure,
    Status,
}

impl PluginOp {
    #[must_use]
    pub fn contract(self) -> &'static OpContract {
        match self {
            Self::Ensure => BuiltinOp::PluginEnsure,
            Self::Status => BuiltinOp::PluginStatus,
        }
        .contract()
    }
}
