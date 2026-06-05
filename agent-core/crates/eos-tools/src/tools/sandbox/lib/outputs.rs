use std::collections::BTreeMap;

use eos_types::{CommandSessionId, JsonObject};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub(in crate::tools::sandbox) struct ReadFileOutput {
    pub(in crate::tools::sandbox) cwd: String,
    pub(in crate::tools::sandbox) file_path: String,
    pub(in crate::tools::sandbox) total_lines: u32,
    pub(in crate::tools::sandbox) start_line: u32,
    pub(in crate::tools::sandbox) end_line: u32,
    pub(in crate::tools::sandbox) content: String,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub(in crate::tools::sandbox) struct MutationOutput {
    pub(in crate::tools::sandbox) cwd: String,
    pub(in crate::tools::sandbox) file_path: String,
    pub(in crate::tools::sandbox) status: String,
    pub(in crate::tools::sandbox) changed_paths: Vec<String>,
    pub(in crate::tools::sandbox) changed_path_kinds: BTreeMap<String, String>,
    pub(in crate::tools::sandbox) mutation_source: String,
    pub(in crate::tools::sandbox) conflict_reason: Option<String>,
    pub(in crate::tools::sandbox) error: JsonObject,
    /// `bytes_written` for `write_file`, `applied_edits` for the edit tools.
    #[serde(flatten)]
    pub(in crate::tools::sandbox) extra: BTreeMap<String, Value>,
}

/// Shared output shape for `exec_command` and `write_stdin`.
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub(in crate::tools::sandbox) struct CommandToolOutput {
    pub(in crate::tools::sandbox) status: String,
    pub(in crate::tools::sandbox) exit_code: Option<i32>,
    pub(in crate::tools::sandbox) output: BTreeMap<String, String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(in crate::tools::sandbox) command_session_id: Option<CommandSessionId>,
    pub(in crate::tools::sandbox) stdout: String,
    pub(in crate::tools::sandbox) stderr: String,
    pub(in crate::tools::sandbox) changed_paths: Vec<String>,
    pub(in crate::tools::sandbox) changed_path_kinds: BTreeMap<String, String>,
    pub(in crate::tools::sandbox) mutation_source: String,
    pub(in crate::tools::sandbox) conflict_reason: Option<String>,
    pub(in crate::tools::sandbox) error: Option<JsonObject>,
}
