use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::request::{require_caller_id, ArgsError};
use crate::CallerId;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunEndInput {
    pub caller: CallerId,
    pub grace_s: Option<f64>,
}

impl RunEndInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            caller: require_caller_id(args)?,
            grace_s: args.get("grace_s").and_then(Value::as_f64),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunCancelAllInput {
    pub grace_s: Option<f64>,
}

impl RunCancelAllInput {
    pub(crate) fn parse(args: &Value) -> Self {
        Self {
            grace_s: args.get("grace_s").and_then(Value::as_f64),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunEndOutput {
    pub success: bool,
    pub caller_id: String,
    pub cancelled_commands: usize,
    pub isolated_exited: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunCancelAllOutput {
    pub success: bool,
    pub cancelled_commands: usize,
    pub isolated_callers_exited: usize,
}
