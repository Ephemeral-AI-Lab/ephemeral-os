use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use super::common::{SandboxRequestBase, SandboxResultBase};

/// Enumerate workspace paths matching a glob pattern.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct GlobRequest {
    /// Caller identity / description / invocation id.
    #[serde(flatten)]
    pub base: SandboxRequestBase,
    /// Glob pattern.
    pub pattern: String,
    /// Optional root path to scope the search.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
}

/// Result of [`GlobRequest`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct GlobResult {
    /// Common result fields.
    #[serde(flatten)]
    pub base: SandboxResultBase,
    /// Matching file paths.
    #[serde(default)]
    pub filenames: Vec<String>,
    /// Count of matching files.
    #[serde(default)]
    pub num_files: u32,
    /// Whether the result was truncated.
    #[serde(default)]
    pub truncated: bool,
}

/// `grep.output_mode` wire values.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum GrepOutputMode {
    /// Return matching file paths and rendered matching lines.
    Content,
    /// Return only matching file paths.
    #[default]
    FilesWithMatches,
    /// Return per-file match counts.
    Count,
}

impl GrepOutputMode {
    /// The daemon wire string for this mode.
    #[must_use]
    pub const fn as_wire(self) -> &'static str {
        match self {
            Self::Content => "content",
            Self::FilesWithMatches => "files_with_matches",
            Self::Count => "count",
        }
    }
}

/// Regex-scan workspace file contents.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct GrepRequest {
    /// Caller identity / description / invocation id.
    #[serde(flatten)]
    pub base: SandboxRequestBase,
    /// Regex pattern (`re`-style; the prompt contract is owned by eos-tools).
    pub pattern: String,
    /// Optional root path to scope the search.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    /// Optional glob filter applied to candidate files.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub glob_filter: Option<String>,
    /// Output mode (defaults to `files_with_matches`).
    #[serde(default = "default_output_mode")]
    pub output_mode: GrepOutputMode,
    /// Cap on returned matches.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub head_limit: Option<u32>,
    /// Offset into the match list.
    #[serde(default)]
    pub offset: u32,
    /// Case-insensitive matching.
    #[serde(default)]
    pub case_insensitive: bool,
    /// Emit line numbers.
    #[serde(default)]
    pub line_numbers: bool,
    /// Multiline matching.
    #[serde(default)]
    pub multiline: bool,
}

/// Result of [`GrepRequest`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct GrepResult {
    /// Common result fields.
    #[serde(flatten)]
    pub base: SandboxResultBase,
    /// Echoed output mode.
    #[serde(default = "default_output_mode")]
    pub output_mode: GrepOutputMode,
    /// Matching file paths.
    #[serde(default)]
    pub filenames: Vec<String>,
    /// Rendered match content (for content output modes).
    #[serde(default)]
    pub content: String,
    /// Count of matching files.
    #[serde(default)]
    pub num_files: u32,
    /// Count of matching lines.
    #[serde(default)]
    pub num_lines: u32,
    /// Count of matches.
    #[serde(default)]
    pub num_matches: u32,
    /// The limit the daemon actually applied, when one was applied.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub applied_limit: Option<u32>,
    /// The offset the daemon actually applied.
    #[serde(default)]
    pub applied_offset: u32,
    /// Whether the result was truncated.
    #[serde(default)]
    pub truncated: bool,
}

fn default_output_mode() -> GrepOutputMode {
    GrepOutputMode::FilesWithMatches
}
