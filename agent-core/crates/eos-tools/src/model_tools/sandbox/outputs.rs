use std::collections::BTreeMap;

use eos_sandbox_api::GrepOutputMode;
use eos_types::JsonObject;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub(super) struct ReadFileOutput {
    pub(super) cwd: String,
    pub(super) file_path: String,
    pub(super) total_lines: u32,
    pub(super) start_line: u32,
    pub(super) end_line: u32,
    pub(super) content: String,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub(super) struct MutationOutput {
    pub(super) cwd: String,
    pub(super) file_path: String,
    pub(super) status: String,
    pub(super) changed_paths: Vec<String>,
    pub(super) changed_path_kinds: BTreeMap<String, String>,
    pub(super) mutation_source: String,
    pub(super) conflict_reason: Option<String>,
    pub(super) error: JsonObject,
    /// `bytes_written` for `write_file`, `applied_edits` for the edit tools.
    #[serde(flatten)]
    pub(super) extra: BTreeMap<String, Value>,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub(super) struct GrepOutput {
    pub(super) cwd: String,
    pub(super) pattern: String,
    pub(super) mode: GrepOutputMode,
    pub(super) filenames: Vec<String>,
    pub(super) content: String,
    pub(super) num_files: u32,
    pub(super) num_lines: u32,
    pub(super) num_matches: u32,
    pub(super) applied_limit: Option<u32>,
    pub(super) applied_offset: u32,
    pub(super) truncated: bool,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub(super) struct GlobOutput {
    pub(super) cwd: String,
    pub(super) pattern: String,
    pub(super) filenames: Vec<String>,
    pub(super) num_files: u32,
    pub(super) truncated: bool,
}

/// `CommandToolOutput` (`command_session_tool.py`).
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub(super) struct CommandToolOutput {
    pub(super) status: String,
    pub(super) exit_code: Option<i32>,
    pub(super) output: BTreeMap<String, String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) command_session_id: Option<String>,
    pub(super) stdout: String,
    pub(super) stderr: String,
    pub(super) changed_paths: Vec<String>,
    pub(super) changed_path_kinds: BTreeMap<String, String>,
    pub(super) mutation_source: String,
    pub(super) conflict_reason: Option<String>,
    pub(super) error: Option<JsonObject>,
}
