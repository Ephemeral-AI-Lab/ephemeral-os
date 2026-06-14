#![forbid(unsafe_code)]

use std::fmt;

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const DEFAULT_CALLER_ID: &str = "default";
pub const MAX_PLUGIN_CALLER_FIELD_CHARS: usize = 256;

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct CallerId(String);

impl CallerId {
    #[must_use]
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    #[must_use]
    pub fn from_wire(args: &Value) -> Self {
        let raw = args
            .get("caller_id")
            .and_then(Value::as_str)
            .unwrap_or(DEFAULT_CALLER_ID);
        Self(raw.trim().to_owned())
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for CallerId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgsError {
    pub key: &'static str,
    pub problem: ArgProblem,
}

impl ArgsError {
    #[must_use]
    pub fn message(&self) -> String {
        match &self.problem {
            ArgProblem::Required => format!("{} is required", self.key),
            ArgProblem::MustBeString => format!("{} must be a string", self.key),
            ArgProblem::MustBeNonEmpty => format!("{} must be non-empty", self.key),
            ArgProblem::MustBeList => format!("{} must be a list", self.key),
            ArgProblem::Invalid(message) => message.clone(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ArgProblem {
    Required,
    MustBeString,
    MustBeNonEmpty,
    MustBeList,
    Invalid(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PluginListInput {
    pub caller: CallerId,
}

impl PluginListInput {
    pub fn parse(args: &Value) -> Result<Self, ArgsError> {
        validate_plugin_caller_fields(args)?;
        Ok(Self {
            caller: CallerId::from_wire(args),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PluginHealthInput {
    pub layer_stack_root: Option<String>,
    pub caller: CallerId,
}

impl PluginHealthInput {
    pub fn parse(args: &Value) -> Result<Self, ArgsError> {
        validate_plugin_caller_fields(args)?;
        Ok(Self {
            layer_stack_root: optional_trimmed_string(args, "layer_stack_root"),
            caller: CallerId::from_wire(args),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LspPositionInput {
    pub line: u64,
    pub character: u64,
}

impl LspPositionInput {
    fn parse(args: &Value, key: &'static str) -> Result<Self, ArgsError> {
        let value = args.get(key).ok_or(ArgsError {
            key,
            problem: ArgProblem::Required,
        })?;
        let Some(object) = value.as_object() else {
            return Err(ArgsError {
                key,
                problem: ArgProblem::Invalid(format!("{key} must be an object")),
            });
        };
        let line = object
            .get("line")
            .and_then(Value::as_u64)
            .ok_or(ArgsError {
                key,
                problem: ArgProblem::Invalid(format!("{key}.line must be a non-negative integer")),
            })?;
        let character = object
            .get("character")
            .and_then(Value::as_u64)
            .ok_or(ArgsError {
                key,
                problem: ArgProblem::Invalid(format!(
                    "{key}.character must be a non-negative integer"
                )),
            })?;
        Ok(Self { line, character })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PyrightLspQuerySymbolsInput {
    pub layer_stack_root: String,
    pub file_path: String,
    pub query: Option<String>,
    pub workspace: bool,
    pub caller: CallerId,
}

impl PyrightLspQuerySymbolsInput {
    pub fn parse(args: &Value) -> Result<Self, ArgsError> {
        validate_plugin_caller_fields(args)?;
        Ok(Self {
            layer_stack_root: required_trimmed_string(args, "layer_stack_root")?,
            file_path: required_trimmed_string(args, "file_path")?,
            query: optional_trimmed_string(args, "query"),
            workspace: args
                .get("workspace")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            caller: CallerId::from_wire(args),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PyrightLspDefinitionInput {
    pub layer_stack_root: String,
    pub file_path: String,
    pub position: LspPositionInput,
    pub caller: CallerId,
}

impl PyrightLspDefinitionInput {
    pub fn parse(args: &Value) -> Result<Self, ArgsError> {
        validate_plugin_caller_fields(args)?;
        Ok(Self {
            layer_stack_root: required_trimmed_string(args, "layer_stack_root")?,
            file_path: required_trimmed_string(args, "file_path")?,
            position: LspPositionInput::parse(args, "position")?,
            caller: CallerId::from_wire(args),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PyrightLspReferencesInput {
    pub layer_stack_root: String,
    pub file_path: String,
    pub position: LspPositionInput,
    pub include_declaration: bool,
    pub caller: CallerId,
}

impl PyrightLspReferencesInput {
    pub fn parse(args: &Value) -> Result<Self, ArgsError> {
        validate_plugin_caller_fields(args)?;
        Ok(Self {
            layer_stack_root: required_trimmed_string(args, "layer_stack_root")?,
            file_path: required_trimmed_string(args, "file_path")?,
            position: LspPositionInput::parse(args, "position")?,
            include_declaration: args
                .get("include_declaration")
                .and_then(Value::as_bool)
                .unwrap_or(true),
            caller: CallerId::from_wire(args),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PyrightLspDiagnosticsInput {
    pub layer_stack_root: String,
    pub file_path: String,
    pub caller: CallerId,
}

impl PyrightLspDiagnosticsInput {
    pub fn parse(args: &Value) -> Result<Self, ArgsError> {
        validate_plugin_caller_fields(args)?;
        Ok(Self {
            layer_stack_root: required_trimmed_string(args, "layer_stack_root")?,
            file_path: required_trimmed_string(args, "file_path")?,
            caller: CallerId::from_wire(args),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PluginListOutput {
    pub success: bool,
    pub providers: Vec<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PluginHealthOutput {
    pub success: bool,
    pub providers: Vec<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PyrightLspQuerySymbolsOutput {
    pub success: bool,
    pub provider: String,
    pub manifest_key: String,
    pub freshness: String,
    pub stale: bool,
    pub symbols: Vec<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PyrightLspLocationsOutput {
    pub success: bool,
    pub provider: String,
    pub manifest_key: String,
    pub freshness: String,
    pub stale: bool,
    pub locations: Vec<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PyrightLspDiagnosticsOutput {
    pub success: bool,
    pub provider: String,
    pub manifest_key: String,
    pub freshness: String,
    pub stale: bool,
    pub diagnostics: Vec<Value>,
}

pub fn validate_plugin_caller_fields(args: &Value) -> Result<(), ArgsError> {
    validate_plugin_caller_value("caller_id", args.get("caller_id"))?;
    let Some(caller) = args.get("caller").and_then(Value::as_object) else {
        return Ok(());
    };
    for (field, value) in caller {
        validate_plugin_caller_value(field, Some(value))?;
    }
    Ok(())
}

fn optional_trimmed_string(args: &Value, key: &str) -> Option<String> {
    args.get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn required_trimmed_string(args: &Value, key: &'static str) -> Result<String, ArgsError> {
    let value = args
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .unwrap_or_default();
    if value.is_empty() {
        return Err(ArgsError {
            key,
            problem: ArgProblem::Required,
        });
    }
    Ok(value.to_owned())
}

fn validate_plugin_caller_value(field: &str, value: Option<&Value>) -> Result<(), ArgsError> {
    let Some(value) = value else {
        return Ok(());
    };
    let Some(text) = value.as_str() else {
        return Err(plugin_caller_error(format!(
            "plugin caller field {field} must be a string"
        )));
    };
    if text.contains('\0') {
        return Err(plugin_caller_error(format!(
            "plugin caller field {field} contains NUL"
        )));
    }
    if text.chars().count() > MAX_PLUGIN_CALLER_FIELD_CHARS {
        return Err(plugin_caller_error(format!(
            "plugin caller field {field} exceeds {MAX_PLUGIN_CALLER_FIELD_CHARS} characters"
        )));
    }
    Ok(())
}

fn plugin_caller_error(message: String) -> ArgsError {
    ArgsError {
        key: "caller",
        problem: ArgProblem::Invalid(message),
    }
}
