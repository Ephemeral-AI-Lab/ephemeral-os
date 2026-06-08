//! Concrete-tool input parsing helpers.

use eos_types::JsonObject;
use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::core::name::ToolName;
use crate::core::result::ToolResult;

/// Parse-and-validate raw tool input into a typed DTO, rendering the Rust
/// "Invalid input for X" in-band message on failure.
///
/// # Errors
/// Returns the in-band [`ToolResult`] error when `raw` does not deserialize.
pub(crate) fn parse_input<T: DeserializeOwned>(
    tool: ToolName,
    raw: &JsonObject,
) -> Result<T, ToolResult> {
    serde_json::from_value::<T>(Value::Object(raw.clone())).map_err(|err| {
        ToolResult::error(format!(
            "Invalid input for {}: {err}. Please retry the tool call with valid arguments.",
            tool.as_str()
        ))
    })
}
