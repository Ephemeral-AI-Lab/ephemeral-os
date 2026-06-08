//! [`ToolResult`] and [`OutputShape`], the per-tool output marker the pipeline
//! validates against.
//!
//! Ports `_framework/core/results.py`. `ToolInputParseResult` collapses into the
//! per-DTO `parse` pattern (each `*Input` returns `Result<Self, String>`, the
//! `String` being the in-band message), so there is no separate parse-result
//! type. `TextToolOutput` becomes [`OutputShape::Text`].

use serde::de::DeserializeOwned;

pub use eos_ports::ToolResult;

/// The declared shape of a tool's successful output (Rust `output_model`).
/// Carried on each `RegisteredTool` so the pipeline can validate output without
/// a per-tool `match` (`validate_tool_output`).
#[derive(Clone)]
pub enum OutputShape {
    /// Plain text — any non-error output is valid (Rust `TextToolOutput`,
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
