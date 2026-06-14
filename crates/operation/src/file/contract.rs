use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::request::{
    optional_path, require_raw_string, require_string, ArgProblem, ArgsError,
};
use crate::CallerId;

use super::SearchReplaceEdit;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReadFileInput {
    pub path: String,
    pub caller: CallerId,
    pub layer_stack_root: Option<PathBuf>,
}

impl ReadFileInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            path: require_string(args, "path")?,
            caller: CallerId::from_wire(args),
            layer_stack_root: optional_path(args, "layer_stack_root"),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WriteFileInput {
    pub path: String,
    pub content: String,
    pub overwrite: bool,
    pub caller: CallerId,
    pub layer_stack_root: Option<PathBuf>,
}

impl WriteFileInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            path: require_string(args, "path")?,
            content: require_raw_string(args, "content")?,
            overwrite: args
                .get("overwrite")
                .and_then(Value::as_bool)
                .unwrap_or(true),
            caller: CallerId::from_wire(args),
            layer_stack_root: optional_path(args, "layer_stack_root"),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EditFileInput {
    pub edits: Vec<SearchReplaceEdit>,
    pub path: String,
    pub caller: CallerId,
    pub layer_stack_root: Option<PathBuf>,
}

impl EditFileInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        let edits = parse_edits(args)?;
        Ok(Self {
            edits,
            path: require_string(args, "path")?,
            caller: CallerId::from_wire(args),
            layer_stack_root: optional_path(args, "layer_stack_root"),
        })
    }
}

fn parse_edits(args: &Value) -> Result<Vec<SearchReplaceEdit>, ArgsError> {
    let edits = args
        .get("edits")
        .and_then(Value::as_array)
        .ok_or_else(|| ArgsError {
            key: "edits",
            problem: ArgProblem::Invalid("edits must be a list".to_owned()),
        })?;
    let mut parsed = Vec::with_capacity(edits.len());
    for raw in edits {
        let edit: SearchReplaceEdit =
            serde_json::from_value(raw.clone()).map_err(|err| ArgsError {
                key: "edits",
                problem: ArgProblem::Invalid(err.to_string()),
            })?;
        if edit.old_text.is_empty() {
            return Err(ArgsError {
                key: "edits",
                problem: ArgProblem::Invalid("edit anchor old_text must be non-empty".to_owned()),
            });
        }
        parsed.push(edit);
    }
    Ok(parsed)
}
