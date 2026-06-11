use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::request::{require_path, require_string, ArgProblem, ArgsError};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LayerMetricsInput {
    pub layer_stack_root: PathBuf,
}

impl LayerMetricsInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            layer_stack_root: require_path(args, "layer_stack_root")?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EnsureBaseInput {
    pub layer_stack_root: PathBuf,
    pub workspace_root: PathBuf,
}

impl EnsureBaseInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            layer_stack_root: require_path(args, "layer_stack_root")?,
            workspace_root: require_path(args, "workspace_root")?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BuildBaseInput {
    pub layer_stack_root: PathBuf,
    pub workspace_root: PathBuf,
    pub reset: bool,
}

impl BuildBaseInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            layer_stack_root: require_path(args, "layer_stack_root")?,
            workspace_root: require_path(args, "workspace_root")?,
            reset: args.get("reset").and_then(Value::as_bool).unwrap_or(false),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CommitToWorkspaceInput {
    pub layer_stack_root: PathBuf,
    pub workspace_root: PathBuf,
}

impl CommitToWorkspaceInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            layer_stack_root: require_path(args, "layer_stack_root")?,
            workspace_root: require_path(args, "workspace_root")?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BindingInput {
    pub layer_stack_root: PathBuf,
}

impl BindingInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            layer_stack_root: require_path(args, "layer_stack_root")?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CommitInput {
    pub layer_stack_root: PathBuf,
    pub workspace_root: PathBuf,
    pub message: String,
    pub paths: Vec<String>,
}

impl CommitInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            layer_stack_root: require_path(args, "layer_stack_root")?,
            workspace_root: require_path(args, "workspace_root")?,
            message: require_string(args, "message")?,
            paths: raw_commit_paths(args)?,
        })
    }
}

fn raw_commit_paths(args: &Value) -> Result<Vec<String>, ArgsError> {
    let Some(value) = args.get("paths") else {
        return Ok(Vec::new());
    };
    match value {
        Value::Null => Ok(Vec::new()),
        Value::String(path) => Ok(vec![path.clone()]),
        Value::Array(items) => items
            .iter()
            .map(|item| {
                item.as_str().map(str::to_owned).ok_or_else(|| ArgsError {
                    key: "paths",
                    problem: ArgProblem::Invalid("paths must be strings".to_owned()),
                })
            })
            .collect(),
        _ => Err(ArgsError {
            key: "paths",
            problem: ArgProblem::Invalid("paths must be a string or array of strings".to_owned()),
        }),
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LayerMetricsOutput {
    pub success: bool,
    pub manifest_version: i64,
    pub manifest_depth: usize,
    pub active_leases: usize,
    pub leased_layers: usize,
    pub layer_dirs: usize,
    pub referenced_layers: usize,
    pub orphan_layer_count: usize,
    pub missing_layer_count: usize,
    pub orphan_layer_ids: Vec<String>,
    pub missing_layer_ids: Vec<String>,
    pub staging_dirs: usize,
    pub storage_bytes: u64,
    pub workspace_bound: bool,
    pub workspace_root: String,
    pub base_root_hash: String,
    pub occ_runtime_service_cache: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkspaceBaseOutput {
    pub success: bool,
    pub created: bool,
    pub binding: Value,
    pub timings: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BindingOutput {
    pub success: bool,
    pub binding: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommitToWorkspaceOutput {
    pub success: bool,
    pub manifest_version: i64,
    pub timings: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommitOutput {
    pub success: bool,
    pub committed: bool,
    pub commit_sha: Option<String>,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub paths: Vec<String>,
    pub worktree_mode: String,
    pub timings: Value,
}
