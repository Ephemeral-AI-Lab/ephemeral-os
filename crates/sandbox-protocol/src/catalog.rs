use serde_json::{json, Map, Value};

use crate::{ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationSpec};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OperationExecutionSpace {
    Manager,
    Runtime,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OperationCatalog {
    pub operation_execution_space: OperationExecutionSpace,
    pub operations: &'static [&'static OperationSpec],
}

impl OperationCatalog {
    #[must_use]
    pub const fn new(
        operation_execution_space: OperationExecutionSpace,
        operations: &'static [&'static OperationSpec],
    ) -> Self {
        Self {
            operation_execution_space,
            operations,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationCatalogDocument {
    pub operation_execution_space: OperationExecutionSpace,
    pub operations: Vec<OperationSpecDocument>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationSpecDocument {
    pub name: String,
    pub summary: String,
    pub args: Vec<ArgSpecDocument>,
    pub cli: Option<CliSpecDocument>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgSpecDocument {
    pub name: String,
    pub kind: ArgKind,
    pub required: bool,
    pub help: String,
    pub default: Option<String>,
    pub cli: Option<ArgCliSpecDocument>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgCliSpecDocument {
    pub flag: Option<String>,
    pub positional: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CliSpecDocument {
    pub path: Vec<String>,
    pub usage: String,
    pub examples: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogDecodeError {
    message: String,
}

impl CatalogDecodeError {
    #[must_use]
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl std::fmt::Display for CatalogDecodeError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for CatalogDecodeError {}

#[must_use]
pub fn catalog_to_value(catalog: OperationCatalog) -> Value {
    json!({
        "operation_execution_space": operation_execution_space_name(catalog.operation_execution_space),
        "operations": catalog
            .operations
            .iter()
            .map(|spec| operation_spec_value(spec))
            .collect::<Vec<_>>(),
    })
}

pub fn catalog_from_value(value: &Value) -> Result<OperationCatalogDocument, CatalogDecodeError> {
    let object = value
        .as_object()
        .ok_or_else(|| decode_error("operation catalog response must be an object"))?;
    let operation_execution_space =
        operation_execution_space_from_name(required_string(object, "operation_execution_space")?)?;
    let operations = required_array(object, "operations")?
        .iter()
        .map(operation_spec_from_value)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(OperationCatalogDocument {
        operation_execution_space,
        operations,
    })
}

#[must_use]
pub const fn operation_execution_space_name(
    operation_execution_space: OperationExecutionSpace,
) -> &'static str {
    match operation_execution_space {
        OperationExecutionSpace::Manager => "manager",
        OperationExecutionSpace::Runtime => "runtime",
    }
}

#[must_use]
pub(crate) const fn catalog_arg_kind_name(kind: ArgKind) -> &'static str {
    match kind {
        ArgKind::String => "string",
        ArgKind::Integer => "integer",
        ArgKind::Float => "float",
        ArgKind::Path => "path",
    }
}

fn operation_spec_value(spec: &OperationSpec) -> Value {
    json!({
        "name": spec.name,
        "summary": spec.summary,
        "args": spec.args.iter().map(arg_spec_value).collect::<Vec<_>>(),
        "cli": spec.cli.map(cli_spec_value),
    })
}

fn arg_spec_value(spec: &ArgSpec) -> Value {
    json!({
        "name": spec.name,
        "kind": catalog_arg_kind_name(spec.kind),
        "required": spec.required,
        "help": spec.help,
        "default": spec.default,
        "cli": spec.cli.map(arg_cli_spec_value),
    })
}

fn cli_spec_value(spec: CliSpec) -> Value {
    json!({
        "path": spec.path,
        "usage": spec.usage,
        "examples": spec.examples,
    })
}

fn arg_cli_spec_value(spec: ArgCliSpec) -> Value {
    json!({
        "flag": spec.flag,
        "positional": spec.positional,
    })
}

fn operation_spec_from_value(value: &Value) -> Result<OperationSpecDocument, CatalogDecodeError> {
    let object = value
        .as_object()
        .ok_or_else(|| decode_error("operation spec must be an object"))?;
    let args = required_array(object, "args")?
        .iter()
        .map(arg_spec_from_value)
        .collect::<Result<Vec<_>, _>>()?;
    let cli = optional_object_value(object, "cli")?
        .map(cli_spec_from_value)
        .transpose()?;
    Ok(OperationSpecDocument {
        name: required_string(object, "name")?.to_owned(),
        summary: required_string(object, "summary")?.to_owned(),
        args,
        cli,
    })
}

fn arg_spec_from_value(value: &Value) -> Result<ArgSpecDocument, CatalogDecodeError> {
    let object = value
        .as_object()
        .ok_or_else(|| decode_error("operation arg spec must be an object"))?;
    let cli = optional_object_value(object, "cli")?
        .map(arg_cli_spec_from_value)
        .transpose()?;
    Ok(ArgSpecDocument {
        name: required_string(object, "name")?.to_owned(),
        kind: arg_kind_from_name(required_string(object, "kind")?)?,
        required: required_bool(object, "required")?,
        help: required_string(object, "help")?.to_owned(),
        default: optional_string(object, "default")?.map(str::to_owned),
        cli,
    })
}

fn cli_spec_from_value(value: &Value) -> Result<CliSpecDocument, CatalogDecodeError> {
    let object = value
        .as_object()
        .ok_or_else(|| decode_error("operation cli spec must be an object"))?;
    let path = required_string_array(object, "path", "operation cli path entries")?;
    let examples = required_string_array(object, "examples", "operation cli examples")?;
    Ok(CliSpecDocument {
        path,
        usage: required_string(object, "usage")?.to_owned(),
        examples,
    })
}

fn arg_cli_spec_from_value(value: &Value) -> Result<ArgCliSpecDocument, CatalogDecodeError> {
    let object = value
        .as_object()
        .ok_or_else(|| decode_error("operation arg cli spec must be an object"))?;
    Ok(ArgCliSpecDocument {
        flag: optional_string(object, "flag")?.map(str::to_owned),
        positional: optional_string(object, "positional")?.map(str::to_owned),
    })
}

fn operation_execution_space_from_name(
    value: &str,
) -> Result<OperationExecutionSpace, CatalogDecodeError> {
    match value {
        "manager" => Ok(OperationExecutionSpace::Manager),
        "runtime" => Ok(OperationExecutionSpace::Runtime),
        other => Err(decode_error(format!(
            "unknown operation_execution_space: {other}"
        ))),
    }
}

fn arg_kind_from_name(value: &str) -> Result<ArgKind, CatalogDecodeError> {
    match value {
        "string" => Ok(ArgKind::String),
        "integer" => Ok(ArgKind::Integer),
        "float" => Ok(ArgKind::Float),
        "path" => Ok(ArgKind::Path),
        other => Err(decode_error(format!("unknown arg kind: {other}"))),
    }
}

fn required_array<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<&'a Vec<Value>, CatalogDecodeError> {
    object
        .get(field)
        .and_then(Value::as_array)
        .ok_or_else(|| decode_error(format!("{field} must be an array")))
}

fn required_string_array(
    object: &Map<String, Value>,
    field: &str,
    entry_label: &str,
) -> Result<Vec<String>, CatalogDecodeError> {
    required_array(object, field)?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| decode_error(format!("{entry_label} must be strings")))
        })
        .collect()
}

fn optional_object_value<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<Option<&'a Value>, CatalogDecodeError> {
    match object.get(field) {
        Some(Value::Null) | None => Ok(None),
        Some(value) if value.is_object() => Ok(Some(value)),
        Some(_) => Err(decode_error(format!("{field} must be an object or null"))),
    }
}

fn required_string<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<&'a str, CatalogDecodeError> {
    object
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| decode_error(format!("{field} must be a string")))
}

fn optional_string<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<Option<&'a str>, CatalogDecodeError> {
    match object.get(field) {
        Some(Value::Null) | None => Ok(None),
        Some(value) => value
            .as_str()
            .map(Some)
            .ok_or_else(|| decode_error(format!("{field} must be a string or null"))),
    }
}

fn required_bool(object: &Map<String, Value>, field: &str) -> Result<bool, CatalogDecodeError> {
    object
        .get(field)
        .and_then(Value::as_bool)
        .ok_or_else(|| decode_error(format!("{field} must be a boolean")))
}

fn decode_error(message: impl Into<String>) -> CatalogDecodeError {
    CatalogDecodeError {
        message: message.into(),
    }
}
