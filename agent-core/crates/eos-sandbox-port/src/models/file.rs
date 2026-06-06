use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use super::common::{SandboxRequestBase, SandboxResultBase};

/// Read one UTF-8 text file.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ReadFileRequest {
    /// Caller identity / description / invocation id.
    #[serde(flatten)]
    pub base: SandboxRequestBase,
    /// Workspace-relative file path to read.
    pub path: String,
}

/// Result of [`ReadFileRequest`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ReadFileResult {
    /// Common result fields.
    #[serde(flatten)]
    pub base: SandboxResultBase,
    /// File contents (empty when the file does not exist).
    pub content: String,
    /// Whether the file existed (fail-closed `false` on a missing daemon field).
    #[serde(default)]
    pub exists: bool,
    /// Content encoding (defaults to `utf-8`).
    pub encoding: String,
}

/// Write one UTF-8 file through OCC.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WriteFileRequest {
    /// Caller identity / description / invocation id.
    #[serde(flatten)]
    pub base: SandboxRequestBase,
    /// Workspace-relative file path to write.
    pub path: String,
    /// New file contents.
    pub content: String,
    /// Whether to overwrite an existing file (defaults to `true`).
    #[serde(default = "default_true")]
    pub overwrite: bool,
}

/// Result of [`WriteFileRequest`] (a guarded mutation).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WriteFileResult {
    /// Common result fields.
    #[serde(flatten)]
    pub base: SandboxResultBase,
    /// Per-path mutation kinds reported by the daemon.
    #[serde(default)]
    pub changed_path_kinds: BTreeMap<String, String>,
    /// Source of the mutation (daemon-reported).
    #[serde(default)]
    pub mutation_source: String,
    /// Guarded-operation status string.
    #[serde(default)]
    pub status: String,
}

/// One exact-match replacement applied as part of an [`EditFileRequest`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct SearchReplaceEdit {
    /// Text to find.
    pub old_text: String,
    /// Replacement text.
    pub new_text: String,
    /// Whether to replace all occurrences (defaults to `false`).
    #[serde(default)]
    pub replace_all: bool,
}

/// Apply search/replace edits through OCC.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct EditFileRequest {
    /// Caller identity / description / invocation id.
    #[serde(flatten)]
    pub base: SandboxRequestBase,
    /// Workspace-relative file path to edit.
    pub path: String,
    /// Ordered list of edits to apply.
    pub edits: Vec<SearchReplaceEdit>,
}

/// Result of [`EditFileRequest`] (a guarded mutation).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct EditFileResult {
    /// Common result fields.
    #[serde(flatten)]
    pub base: SandboxResultBase,
    /// Per-path mutation kinds reported by the daemon.
    #[serde(default)]
    pub changed_path_kinds: BTreeMap<String, String>,
    /// Source of the mutation (daemon-reported).
    #[serde(default)]
    pub mutation_source: String,
    /// Guarded-operation status string.
    #[serde(default)]
    pub status: String,
    /// Number of edits applied (defaults to `0`).
    #[serde(default)]
    pub applied_edits: u32,
}

fn default_true() -> bool {
    true
}
