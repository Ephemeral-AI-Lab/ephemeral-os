//! [`ToolResult`] — the normalized in-band tool result — and [`OutputShape`],
//! the per-tool output marker the pipeline validates against.
//!
//! Ports `_framework/core/results.py`. `ToolInputParseResult` collapses into the
//! per-DTO `parse` pattern (each `*Input` returns `Result<Self, String>`, the
//! `String` being the in-band message), so there is no separate parse-result
//! type. `TextToolOutput` becomes [`OutputShape::Text`].

use eos_types::JsonObject;
use serde::de::DeserializeOwned;

/// A normalized tool result. Both success and tool-domain failure are values of
/// this type; only framework faults are `Err(ToolError)` (`error.rs`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolResult {
    /// The model-facing output text.
    pub output: String,
    /// Whether this is an in-band (tool-domain) error.
    pub is_error: bool,
    /// Heterogeneous result metadata (`submission_kind`, `policy`,
    /// `command_session_id`, `output_validation_error`, …). Stays a
    /// [`JsonObject`] (transitional, anchor §4) because hooks/audit stamp
    /// open-ended keys.
    pub metadata: JsonObject,
    /// Set to `true` by the stamp stage iff the tool is terminal and succeeded.
    /// The single source of the loop's `TOOL_STOP` signal.
    pub is_terminal: bool,
}

impl ToolResult {
    /// A successful plain result.
    #[must_use]
    pub fn ok(output: impl Into<String>) -> Self {
        Self {
            output: output.into(),
            is_error: false,
            metadata: JsonObject::new(),
            is_terminal: false,
        }
    }

    /// An in-band (tool-domain) error result.
    #[must_use]
    pub fn error(output: impl Into<String>) -> Self {
        Self {
            output: output.into(),
            is_error: true,
            metadata: JsonObject::new(),
            is_terminal: false,
        }
    }

    /// Attach result metadata (builder-style).
    #[must_use]
    pub fn with_metadata(mut self, metadata: JsonObject) -> Self {
        self.metadata = metadata;
        self
    }

    /// Insert one metadata key (builder-style).
    #[must_use]
    pub fn meta(mut self, key: impl Into<String>, value: serde_json::Value) -> Self {
        self.metadata.insert(key.into(), value);
        self
    }
}

/// The declared shape of a tool's successful output (Python `output_model`).
/// Carried on each `RegisteredTool` so the pipeline can validate output without
/// a per-tool `match` (`validate_tool_output`).
#[derive(Clone)]
pub enum OutputShape {
    /// Plain text — any non-error output is valid (Python `TextToolOutput`,
    /// a `RootModel[str]`).
    Text,
    /// Structured JSON that must deserialize into the named model.
    Json {
        /// The output model name (for the validation message).
        model_name: &'static str,
        /// Validator: `Ok(())` if the output string parses into the model, else
        /// `Err(message)`.
        validate: fn(&str) -> Result<(), String>,
    },
}

impl std::fmt::Debug for OutputShape {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OutputShape::Text => f.write_str("OutputShape::Text"),
            OutputShape::Json { model_name, .. } => {
                write!(f, "OutputShape::Json({model_name})")
            }
        }
    }
}

impl OutputShape {
    /// Build a [`OutputShape::Json`] for output model `T`.
    #[must_use]
    pub fn json<T: DeserializeOwned>(model_name: &'static str) -> Self {
        OutputShape::Json {
            model_name,
            validate: validate_json::<T>,
        }
    }
}

/// Parse `output` as `T`, discarding the value — used as the `OutputShape::Json`
/// validator function pointer.
fn validate_json<T: DeserializeOwned>(output: &str) -> Result<(), String> {
    serde_json::from_str::<T>(output)
        .map(|_| ())
        .map_err(|err| err.to_string())
}
