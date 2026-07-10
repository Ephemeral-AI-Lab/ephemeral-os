use sandbox_protocol::{
    catalog_from_value, catalog_to_value, error_kind, error_response_with_details, ArgKind,
    CliOperationCatalog, CliOperationCatalogDocument, CliOperationExecutionSpace,
    CliOperationScope, CliOperationSpecDocument, Request,
};
use serde_json::{Map, Number, Value};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuildRequestInput {
    pub execution_space: CliOperationExecutionSpace,
    pub operation: String,
    pub operation_argv: Vec<String>,
    pub sandbox_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuildRequestValueInput {
    pub execution_space: CliOperationExecutionSpace,
    pub operation: String,
    pub arguments: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestBuildError {
    message: String,
}

impl RequestBuildError {
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        error_kind::INVALID_REQUEST
    }

    #[must_use]
    pub fn message(&self) -> &str {
        &self.message
    }

    #[must_use]
    pub fn to_error_envelope(&self) -> Value {
        error_response_with_details(self.kind(), self.message.clone(), Value::Object(Map::new()))
    }
}

impl std::fmt::Display for RequestBuildError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for RequestBuildError {}

/// Convert a static catalog into its owned document form (round-tripping through
/// the protocol's JSON encoding, which also runs catalog validation).
///
/// # Errors
/// Returns an error when the catalog fails protocol validation.
pub fn catalog_document(
    catalog: CliOperationCatalog,
) -> Result<CliOperationCatalogDocument, RequestBuildError> {
    catalog_from_value(&catalog_to_value(catalog)).map_err(|error| build_error(error.message()))
}

/// Build a wire request for `input` against `catalog`, minting a fresh request id.
///
/// # Errors
/// Returns an error for unknown operations, argument parse failures, or a
/// missing/empty runtime sandbox id.
pub fn build_request_from_catalog(
    input: BuildRequestInput,
    catalog: &CliOperationCatalogDocument,
) -> Result<Request, RequestBuildError> {
    build_request_from_catalog_with_id(input, catalog, next_request_id())
}

/// Build a wire request for `input` against `catalog` with an explicit request id.
///
/// # Errors
/// Returns an error for unknown operations, argument parse failures, or a
/// missing/empty runtime sandbox id.
pub fn build_request_from_catalog_with_id(
    input: BuildRequestInput,
    catalog: &CliOperationCatalogDocument,
    request_id: impl Into<String>,
) -> Result<Request, RequestBuildError> {
    let spec = operation_spec(input.execution_space, &input.operation, catalog)?;
    let args = build_args(spec, &input.operation_argv)?;
    build_scoped_request(
        input.execution_space,
        spec,
        args,
        input.sandbox_id,
        request_id,
    )
}

/// Build a wire request from an object of typed argument values, minting a
/// fresh request id.
///
/// # Errors
/// Returns an error for a non-object input, unknown operation or argument,
/// invalid scalar type, missing required value, or invalid sandbox selector.
pub fn build_request_from_values(
    input: BuildRequestValueInput,
    catalog: &CliOperationCatalogDocument,
) -> Result<Request, RequestBuildError> {
    build_request_from_values_with_id(input, catalog, next_request_id())
}

/// Build a wire request from an object of typed argument values with an
/// explicit request id.
///
/// Runtime `sandbox_id` is a transport selector rather than an operation
/// argument. Observability selectors are catalog arguments and are removed by
/// the shared observability routing step.
///
/// # Errors
/// Returns an error for a non-object input, unknown operation or argument,
/// invalid scalar type, missing required value, or invalid sandbox selector.
pub fn build_request_from_values_with_id(
    input: BuildRequestValueInput,
    catalog: &CliOperationCatalogDocument,
    request_id: impl Into<String>,
) -> Result<Request, RequestBuildError> {
    let spec = operation_spec(input.execution_space, &input.operation, catalog)?;
    let Value::Object(mut values) = input.arguments else {
        return Err(build_error(format!(
            "arguments for {} must be an object",
            input.operation
        )));
    };
    let sandbox_id = if input.execution_space == CliOperationExecutionSpace::Runtime {
        Some(take_runtime_sandbox_id(&mut values)?)
    } else {
        None
    };
    let args = build_args_from_values(spec, values)?;
    build_scoped_request(input.execution_space, spec, args, sandbox_id, request_id)
}

fn build_scoped_request(
    execution_space: CliOperationExecutionSpace,
    spec: &CliOperationSpecDocument,
    args: Value,
    sandbox_id: Option<String>,
    request_id: impl Into<String>,
) -> Result<Request, RequestBuildError> {
    match execution_space {
        CliOperationExecutionSpace::Manager => Ok(Request::new(
            &spec.name,
            request_id,
            CliOperationScope::system(),
            args,
        )),
        CliOperationExecutionSpace::Runtime => Ok(Request::new(
            &spec.name,
            request_id,
            CliOperationScope::sandbox(resolve_runtime_sandbox_id(sandbox_id)?),
            args,
        )),
        CliOperationExecutionSpace::Observability => {
            build_observability_request(&spec.name, args, request_id)
        }
    }
}

fn operation_spec<'a>(
    execution_space: CliOperationExecutionSpace,
    operation: &str,
    catalog: &'a CliOperationCatalogDocument,
) -> Result<&'a CliOperationSpecDocument, RequestBuildError> {
    if execution_space != catalog.operation_execution_space {
        return Err(build_error(format!(
            "loaded catalog is for {}, not {}",
            sandbox_protocol::operation_execution_space_name(catalog.operation_execution_space),
            sandbox_protocol::operation_execution_space_name(execution_space)
        )));
    }
    if operation == "help" {
        return Err(build_error(
            "help is reserved and cannot be used as an operation name",
        ));
    }
    find_cli_operation_spec(catalog, operation)
}

/// Build the wire request for the read-only `observability` space.
///
/// Sandbox-scoped views resolve to the daemon op `get_observability`; the
/// operation name becomes the `view` param, and `--sandbox-id` is CLI routing
/// (it selects the daemon) rather than an op param. `snapshot` without
/// `--sandbox-id` is manager-owned and aggregates ready sandboxes.
fn build_observability_request(
    view: &str,
    args: Value,
    request_id: impl Into<String>,
) -> Result<Request, RequestBuildError> {
    let Value::Object(mut args) = args else {
        return Err(build_error("observability arguments must be an object"));
    };
    let sandbox_id = match args.remove("sandbox_id") {
        Some(Value::String(sandbox_id)) if !sandbox_id.trim().is_empty() => Some(sandbox_id),
        Some(Value::String(_)) => return Err(build_error("--sandbox-id must be non-empty")),
        Some(_) => return Err(build_error("--sandbox-id must be a string")),
        None if view == OBSERVABILITY_SNAPSHOT_OP => None,
        None => return Err(build_error("observability operations require --sandbox-id")),
    };
    let Some(sandbox_id) = sandbox_id else {
        return Ok(Request::new(
            OBSERVABILITY_SNAPSHOT_OP,
            request_id,
            CliOperationScope::system(),
            Value::Object(args),
        ));
    };
    args.insert("view".to_owned(), Value::String(view.to_owned()));
    Ok(Request::new(
        OBSERVABILITY_OP,
        request_id,
        CliOperationScope::sandbox(sandbox_id),
        Value::Object(args),
    ))
}

const OBSERVABILITY_OP: &str = "get_observability";
const OBSERVABILITY_SNAPSHOT_OP: &str = "snapshot";

/// Resolve the sandbox id for a runtime operation.
///
/// Runtime operations require an explicit `--sandbox-id`; there is no config or
/// environment fallback.
///
/// # Errors
/// Returns an error when the sandbox id is absent or empty.
pub fn resolve_runtime_sandbox_id(sandbox_id: Option<String>) -> Result<String, RequestBuildError> {
    let sandbox_id =
        sandbox_id.ok_or_else(|| build_error("runtime operations require --sandbox-id"))?;
    if sandbox_id.trim().is_empty() {
        Err(build_error("runtime sandbox id must be non-empty"))
    } else {
        Ok(sandbox_id)
    }
}

fn build_args(
    spec: &CliOperationSpecDocument,
    argv: &[String],
) -> Result<Value, RequestBuildError> {
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

    finish_args(spec, values, true)
}

fn build_args_from_values(
    spec: &CliOperationSpecDocument,
    mut supplied: Map<String, Value>,
) -> Result<Value, RequestBuildError> {
    let mut unknown = supplied
        .keys()
        .filter(|name| !spec.args.iter().any(|arg| arg.name == name.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    unknown.sort_unstable();
    if let Some(name) = unknown.first() {
        return Err(build_error(format!(
            "unknown argument for {}: {name}",
            spec.name
        )));
    }

    let mut values = Map::new();
    for arg in &spec.args {
        if let Some(value) = supplied.remove(&arg.name) {
            values.insert(arg.name.clone(), validate_arg_value(arg, value, &arg.name)?);
        }
    }
    finish_args(spec, values, false)
}

fn finish_args(
    spec: &CliOperationSpecDocument,
    mut values: Map<String, Value>,
    cli_names: bool,
) -> Result<Value, RequestBuildError> {
    for arg in &spec.args {
        if values.contains_key(&arg.name) {
            continue;
        }
        let name = if cli_names {
            cli_arg_name(arg)
        } else {
            &arg.name
        };
        if let Some(default) = catalog_arg_default(arg)? {
            values.insert(arg.name.clone(), default);
        } else if arg.required {
            return Err(build_error(format!("{name} is required for {}", spec.name)));
        }
    }

    Ok(Value::Object(values))
}

/// Parse an argument's catalog default into the same native JSON value used by
/// CLI and MCP request construction.
///
/// # Errors
/// Returns an error when a declared default does not match its catalog kind.
pub fn catalog_arg_default(
    arg: &sandbox_protocol::ArgSpecDocument,
) -> Result<Option<Value>, RequestBuildError> {
    arg.default
        .as_deref()
        .map(|default| parse_arg_value(arg, default, &arg.name))
        .transpose()
}

fn insert_arg_value(
    values: &mut Map<String, Value>,
    arg: &sandbox_protocol::ArgSpecDocument,
    value: &str,
) -> Result<(), RequestBuildError> {
    if values.contains_key(&arg.name) {
        return Err(build_error(format!(
            "{} was provided more than once",
            cli_arg_name(arg)
        )));
    }
    values.insert(
        arg.name.clone(),
        parse_arg_value(arg, value, cli_arg_name(arg))?,
    );
    Ok(())
}

fn parse_arg_value(
    arg: &sandbox_protocol::ArgSpecDocument,
    value: &str,
    name: &str,
) -> Result<Value, RequestBuildError> {
    let value = match arg.kind {
        ArgKind::String | ArgKind::Path => Ok(Value::String(value.to_owned())),
        ArgKind::Integer => value.parse::<u64>().map_or_else(
            |_| Err(build_error(format!("{name} must be an unsigned integer"))),
            |number| Ok(Value::Number(Number::from(number))),
        ),
        ArgKind::Float => {
            let parsed = value
                .parse::<f64>()
                .map_err(|_| build_error(format!("{name} must be a finite number")))?;
            Number::from_f64(parsed)
                .map(Value::Number)
                .ok_or_else(|| build_error(format!("{name} must be finite")))
        }
        ArgKind::JsonArray => serde_json::from_str::<Value>(value)
            .map_err(|_| build_error(format!("{name} must be a JSON array"))),
    }?;
    validate_arg_value(arg, value, name)
}

fn validate_arg_value(
    arg: &sandbox_protocol::ArgSpecDocument,
    value: Value,
    name: &str,
) -> Result<Value, RequestBuildError> {
    let valid = match arg.kind {
        ArgKind::String | ArgKind::Path => value.is_string(),
        ArgKind::Integer => value.as_u64().is_some(),
        ArgKind::Float => value.as_f64().is_some_and(f64::is_finite),
        ArgKind::JsonArray => value.is_array(),
    };
    if valid {
        return Ok(value);
    }
    let expected = match arg.kind {
        ArgKind::String | ArgKind::Path => "a string",
        ArgKind::Integer => "an unsigned integer",
        ArgKind::Float => "a finite number",
        ArgKind::JsonArray => "a JSON array",
    };
    Err(build_error(format!("{name} must be {expected}")))
}

fn take_runtime_sandbox_id(values: &mut Map<String, Value>) -> Result<String, RequestBuildError> {
    match values.remove("sandbox_id") {
        Some(Value::String(sandbox_id)) if !sandbox_id.trim().is_empty() => Ok(sandbox_id),
        Some(Value::String(_)) => Err(build_error("sandbox_id must be non-empty")),
        Some(_) => Err(build_error("sandbox_id must be a string")),
        None => Err(build_error("sandbox_id is required for runtime operations")),
    }
}

fn find_flag_arg<'a>(
    spec: &'a CliOperationSpecDocument,
    flag: &str,
) -> Result<&'a sandbox_protocol::ArgSpecDocument, RequestBuildError> {
    spec.args
        .iter()
        .find(|arg| arg.cli.as_ref().and_then(|cli| cli.flag.as_deref()) == Some(flag))
        .or_else(|| legacy_flag_arg(spec, flag))
        .ok_or_else(|| build_error(format!("unknown flag for {}: {flag}", spec.name)))
}

fn legacy_flag_arg<'a>(
    spec: &'a CliOperationSpecDocument,
    flag: &str,
) -> Option<&'a sandbox_protocol::ArgSpecDocument> {
    if spec.name == "create_sandbox" && flag == "--workspace-root" {
        return spec.args.iter().find(|arg| arg.name == "workspace_root");
    }
    None
}

fn find_cli_operation_spec<'a>(
    catalog: &'a CliOperationCatalogDocument,
    operation: &str,
) -> Result<&'a CliOperationSpecDocument, RequestBuildError> {
    catalog
        .operations
        .iter()
        .find(|spec| spec.name == operation)
        .ok_or_else(|| build_error(format!("unknown operation: {operation}")))
}

fn cli_arg_name(arg: &sandbox_protocol::ArgSpecDocument) -> &str {
    arg.cli
        .as_ref()
        .and_then(|cli| cli.flag.as_deref().or(cli.positional.as_deref()))
        .unwrap_or(&arg.name)
}

fn next_request_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

fn build_error(message: impl Into<String>) -> RequestBuildError {
    RequestBuildError {
        message: message.into(),
    }
}
