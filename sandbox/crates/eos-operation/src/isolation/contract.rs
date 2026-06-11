use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::request::{require_caller_id, require_path, ArgsError};
use crate::CallerId;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IsolationEnterInput {
    pub caller: CallerId,
    pub layer_stack_root: PathBuf,
}

impl IsolationEnterInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            caller: require_caller_id(args)?,
            layer_stack_root: require_path(args, "layer_stack_root")?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IsolationExitInput {
    pub caller: CallerId,
    pub grace_s: Option<f64>,
}

impl IsolationExitInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            caller: require_caller_id(args)?,
            grace_s: args.get("grace_s").and_then(Value::as_f64),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IsolationStatusInput {
    pub caller: CallerId,
}

impl IsolationStatusInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            caller: require_caller_id(args)?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IsolationEnterOutput {
    pub success: bool,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub workspace_handle_id: String,
    pub workspace_root: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IsolationExitOutput {
    pub success: bool,
    pub evicted_upperdir_bytes: u64,
    pub lifetime_s: f64,
    pub total_ms: f64,
    pub phases_ms: Value,
    pub inspection: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum IsolationStatusOutput {
    Open {
        success: bool,
        open: bool,
        manifest_version: i64,
        manifest_root_hash: String,
        workspace_root: String,
        created_at: f64,
        last_activity: f64,
    },
    Closed {
        success: bool,
        open: bool,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ListOpenOutput {
    pub success: bool,
    pub open_caller_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TestResetOutput {
    pub success: bool,
    pub reset: bool,
    pub exited_callers: Vec<String>,
}
