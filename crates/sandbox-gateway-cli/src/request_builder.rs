use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use sandbox_protocol::{OperationScope, Request};
use serde_json::{json, Map, Number, Value};

use crate::config::GatewayConfig;

static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(1);

const DESCRIBE_MANAGER_OPERATIONS: &str = "describe_manager_operations";
const DESCRIBE_DAEMON_OPERATIONS: &str = "describe_daemon_operations";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecutionSpace {
    Manager,
    Runtime,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuildRequestInput {
    pub execution_space: ExecutionSpace,
    pub operation: String,
    pub operation_argv: Vec<String>,
    pub sandbox_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationCatalogDocument {
    pub operation_execution_space: ExecutionSpace,
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
    pub kind: ArgKindDocument,
    pub required: bool,
    pub help: String,
    pub default: Option<String>,
    pub cli: Option<ArgCliSpecDocument>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArgKindDocument {
    String,
    Integer,
    Float,
    Path,
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
pub struct RequestBuildError {
    message: String,
}

impl RequestBuildError {
    #[must_use]
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl std::fmt::Display for RequestBuildError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for RequestBuildError {}

pub fn manager_catalog_request() -> Request {
    manager_catalog_request_with_id(next_request_id())
}

pub fn manager_catalog_request_with_id(request_id: impl Into<String>) -> Request {
    Request::new(
        DESCRIBE_MANAGER_OPERATIONS,
        request_id,
        OperationScope::system(),
        json!({}),
    )
}

pub fn runtime_catalog_request(sandbox_id: impl Into<String>) -> Request {
    runtime_catalog_request_with_id(sandbox_id, next_request_id())
}

pub fn runtime_catalog_request_with_id(
    sandbox_id: impl Into<String>,
    request_id: impl Into<String>,
) -> Request {
    Request::new(
        DESCRIBE_DAEMON_OPERATIONS,
        request_id,
        OperationScope::system(),
        json!({ "sandbox_id": sandbox_id.into() }),
    )
}

pub fn build_request_from_catalog(
    input: BuildRequestInput,
    config: &GatewayConfig,
    catalog: &OperationCatalogDocument,
) -> Result<Request, RequestBuildError> {
    build_request_from_catalog_with_id(input, config, catalog, next_request_id())
}

pub fn build_request_from_catalog_with_id(
    input: BuildRequestInput,
    config: &GatewayConfig,
    catalog: &OperationCatalogDocument,
    request_id: impl Into<String>,
) -> Result<Request, RequestBuildError> {
    if input.execution_space != catalog.operation_execution_space {
        return Err(build_error(format!(
            "loaded catalog is for {}, not {}",
            execution_space_name(catalog.operation_execution_space),
            execution_space_name(input.execution_space)
        )));
    }
    let spec = find_operation_spec(catalog, &input.operation)?;
    let args = build_args(spec, &input.operation_argv)?;
    let scope = match input.execution_space {
        ExecutionSpace::Manager => OperationScope::system(),
        ExecutionSpace::Runtime => {
            OperationScope::sandbox(resolve_runtime_sandbox_id(input.sandbox_id, config)?)
        }
    };

    Ok(Request::new(&spec.name, request_id, scope, args))
}

pub fn resolve_runtime_sandbox_id(
    sandbox_id: Option<String>,
    config: &GatewayConfig,
) -> Result<String, RequestBuildError> {
    let sandbox_id = sandbox_id
        .or_else(|| config.default_sandbox_id.clone())
        .ok_or_else(|| {
            build_error("runtime operations require --sandbox-id or SANDBOX_DEFAULT_ID")
        })?;
    if sandbox_id.trim().is_empty() {
        Err(build_error("runtime sandbox id must be non-empty"))
    } else {
        Ok(sandbox_id)
    }
}

pub fn catalog_from_response(
    response: &Value,
) -> Result<OperationCatalogDocument, RequestBuildError> {
    if response.get("error").is_some() {
        return Err(build_error(format!(
            "operation catalog request failed: {response}"
        )));
    }
    let object = response
        .as_object()
        .ok_or_else(|| build_error("operation catalog response must be an object"))?;
    let operation_execution_space = match required_string(object, "operation_execution_space")? {
        "manager" => ExecutionSpace::Manager,
        "runtime" => ExecutionSpace::Runtime,
        other => {
            return Err(build_error(format!(
                "unknown operation_execution_space: {other}"
            )))
        }
    };
    let operations = required_array(object, "operations")?
        .iter()
        .map(operation_spec_from_value)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(OperationCatalogDocument {
        operation_execution_space,
        operations,
    })
}

fn operation_spec_from_value(value: &Value) -> Result<OperationSpecDocument, RequestBuildError> {
    let object = value
        .as_object()
        .ok_or_else(|| build_error("operation spec must be an object"))?;
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

fn arg_spec_from_value(value: &Value) -> Result<ArgSpecDocument, RequestBuildError> {
    let object = value
        .as_object()
        .ok_or_else(|| build_error("operation arg spec must be an object"))?;
    let kind = match required_string(object, "kind")? {
        "string" => ArgKindDocument::String,
        "integer" => ArgKindDocument::Integer,
        "float" => ArgKindDocument::Float,
        "path" => ArgKindDocument::Path,
        other => return Err(build_error(format!("unknown arg kind: {other}"))),
    };
    let cli = optional_object_value(object, "cli")?
        .map(arg_cli_spec_from_value)
        .transpose()?;
    Ok(ArgSpecDocument {
        name: required_string(object, "name")?.to_owned(),
        kind,
        required: required_bool(object, "required")?,
        help: required_string(object, "help")?.to_owned(),
        default: optional_string(object, "default")?.map(str::to_owned),
        cli,
    })
}

fn cli_spec_from_value(value: &Value) -> Result<CliSpecDocument, RequestBuildError> {
    let object = value
        .as_object()
        .ok_or_else(|| build_error("operation cli spec must be an object"))?;
    let path = required_array(object, "path")?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| build_error("operation cli path entries must be strings"))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let examples = required_array(object, "examples")?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| build_error("operation cli examples must be strings"))
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(CliSpecDocument {
        path,
        usage: required_string(object, "usage")?.to_owned(),
        examples,
    })
}

fn arg_cli_spec_from_value(value: &Value) -> Result<ArgCliSpecDocument, RequestBuildError> {
    let object = value
        .as_object()
        .ok_or_else(|| build_error("operation arg cli spec must be an object"))?;
    Ok(ArgCliSpecDocument {
        flag: optional_string(object, "flag")?.map(str::to_owned),
        positional: optional_string(object, "positional")?.map(str::to_owned),
    })
}

fn build_args(spec: &OperationSpecDocument, argv: &[String]) -> Result<Value, RequestBuildError> {
    let mut values = Map::new();
    let positional_args = spec
        .args
        .iter()
        .filter(|arg| {
            arg.cli
                .as_ref()
                .and_then(|cli| cli.positional.as_ref())
                .is_some()
        })
        .collect::<Vec<_>>();
    let mut next_positional = 0usize;
    let mut index = 0usize;

    while index < argv.len() {
        let token = &argv[index];
        if token.starts_with("--") {
            let arg = find_flag_arg(spec, token)?;
            index = index.saturating_add(1);
            let value = argv
                .get(index)
                .ok_or_else(|| build_error(format!("{token} requires a value")))?;
            insert_arg_value(&mut values, arg, value)?;
        } else {
            let arg = positional_args.get(next_positional).ok_or_else(|| {
                build_error(format!(
                    "unexpected positional argument for {}: {token}",
                    spec.name
                ))
            })?;
            next_positional = next_positional.saturating_add(1);
            insert_arg_value(&mut values, arg, token)?;
        }
        index = index.saturating_add(1);
    }

    for arg in &spec.args {
        if values.contains_key(&arg.name) {
            continue;
        }
        if let Some(default) = &arg.default {
            values.insert(arg.name.clone(), parse_arg_value(arg, default)?);
        } else if arg.required {
            return Err(build_error(format!(
                "{} is required for {}",
                cli_arg_name(arg),
                spec.name
            )));
        }
    }

    Ok(Value::Object(values))
}

fn insert_arg_value(
    values: &mut Map<String, Value>,
    arg: &ArgSpecDocument,
    value: &str,
) -> Result<(), RequestBuildError> {
    if values.contains_key(&arg.name) {
        return Err(build_error(format!(
            "{} was provided more than once",
            cli_arg_name(arg)
        )));
    }
    values.insert(arg.name.clone(), parse_arg_value(arg, value)?);
    Ok(())
}

fn parse_arg_value(arg: &ArgSpecDocument, value: &str) -> Result<Value, RequestBuildError> {
    match arg.kind {
        ArgKindDocument::String | ArgKindDocument::Path => Ok(Value::String(value.to_owned())),
        ArgKindDocument::Integer => value.parse::<u64>().map_or_else(
            |_| {
                Err(build_error(format!(
                    "{} must be an unsigned integer",
                    cli_arg_name(arg)
                )))
            },
            |number| Ok(Value::Number(Number::from(number))),
        ),
        ArgKindDocument::Float => {
            let parsed = value.parse::<f64>().map_err(|_| {
                build_error(format!("{} must be a finite number", cli_arg_name(arg)))
            })?;
            Number::from_f64(parsed)
                .map(Value::Number)
                .ok_or_else(|| build_error(format!("{} must be finite", cli_arg_name(arg))))
        }
    }
}

fn find_flag_arg<'a>(
    spec: &'a OperationSpecDocument,
    flag: &str,
) -> Result<&'a ArgSpecDocument, RequestBuildError> {
    spec.args
        .iter()
        .find(|arg| arg.cli.as_ref().and_then(|cli| cli.flag.as_deref()) == Some(flag))
        .ok_or_else(|| build_error(format!("unknown flag for {}: {flag}", spec.name)))
}

fn find_operation_spec<'a>(
    catalog: &'a OperationCatalogDocument,
    operation: &str,
) -> Result<&'a OperationSpecDocument, RequestBuildError> {
    catalog
        .operations
        .iter()
        .find(|spec| spec.name == operation)
        .ok_or_else(|| build_error(format!("unknown operation: {operation}")))
}

fn cli_arg_name(arg: &ArgSpecDocument) -> &str {
    arg.cli
        .as_ref()
        .and_then(|cli| cli.flag.as_deref().or(cli.positional.as_deref()))
        .unwrap_or(&arg.name)
}

fn required_array<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<&'a Vec<Value>, RequestBuildError> {
    object
        .get(field)
        .and_then(Value::as_array)
        .ok_or_else(|| build_error(format!("{field} must be an array")))
}

fn optional_object_value<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<Option<&'a Value>, RequestBuildError> {
    match object.get(field) {
        Some(Value::Null) | None => Ok(None),
        Some(value) if value.is_object() => Ok(Some(value)),
        Some(_) => Err(build_error(format!("{field} must be an object or null"))),
    }
}

fn required_string<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<&'a str, RequestBuildError> {
    object
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| build_error(format!("{field} must be a string")))
}

fn optional_string<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<Option<&'a str>, RequestBuildError> {
    match object.get(field) {
        Some(Value::Null) | None => Ok(None),
        Some(value) => value
            .as_str()
            .map(Some)
            .ok_or_else(|| build_error(format!("{field} must be a string or null"))),
    }
}

fn required_bool(object: &Map<String, Value>, field: &str) -> Result<bool, RequestBuildError> {
    object
        .get(field)
        .and_then(Value::as_bool)
        .ok_or_else(|| build_error(format!("{field} must be a boolean")))
}

fn execution_space_name(execution_space: ExecutionSpace) -> &'static str {
    match execution_space {
        ExecutionSpace::Manager => "manager",
        ExecutionSpace::Runtime => "runtime",
    }
}

fn next_request_id() -> String {
    let counter = REQUEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_millis());
    format!("sandbox-cli-{}-{millis}-{counter}", std::process::id())
}

fn build_error(message: impl Into<String>) -> RequestBuildError {
    RequestBuildError {
        message: message.into(),
    }
}
