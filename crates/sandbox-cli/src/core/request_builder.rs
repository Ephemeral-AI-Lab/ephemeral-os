use sandbox_operation_contract::{
    catalog_from_value, catalog_to_value, error, error_response_with_details,
    operation_domain_name, ArgKind, ArgSpecDocument, OperationCatalog, OperationCatalogDocument,
    OperationDomain, OperationRequest, OperationScope, OperationSpecDocument,
};
use serde_json::{Map, Number, Value};

use crate::projection::document::{operation_projection, CatalogDocument, ProjectionError};
use crate::projection::{ArgumentProjection, OperationProjection};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuildRequestInput {
    pub execution_space: OperationDomain,
    pub operation: String,
    pub operation_argv: Vec<String>,
    pub sandbox_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuildRequestValueInput {
    pub execution_space: OperationDomain,
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
        error::INVALID_REQUEST
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

impl From<ProjectionError> for RequestBuildError {
    fn from(error: ProjectionError) -> Self {
        build_error(error.message())
    }
}

/// Convert a static semantic catalog into its validated owned document form.
///
/// # Errors
/// Returns an error when the catalog fails validation.
pub fn catalog_document(
    catalog: OperationCatalog,
) -> Result<OperationCatalogDocument, RequestBuildError> {
    catalog_from_value(&catalog_to_value(catalog)).map_err(|error| build_error(error.message()))
}

/// Build a wire request for `input` against `catalog`, minting a fresh request id.
///
/// # Errors
/// Returns an error for unknown operations, argument parse failures, or a
/// missing/empty runtime sandbox id.
pub fn build_request_from_catalog(
    input: BuildRequestInput,
    catalog: &CatalogDocument,
) -> Result<OperationRequest, RequestBuildError> {
    build_request_from_catalog_with_id(input, catalog, next_request_id())
}

/// Build a wire request for `input` against `catalog` with an explicit request id.
///
/// # Errors
/// Returns an error for unknown operations, argument parse failures, or a
/// missing/empty runtime sandbox id.
pub fn build_request_from_catalog_with_id(
    input: BuildRequestInput,
    catalog: &CatalogDocument,
    request_id: impl Into<String>,
) -> Result<OperationRequest, RequestBuildError> {
    let (spec, projection) = projected_operation(input.execution_space, &input.operation, catalog)?;
    let args = build_args(spec, projection, &input.operation_argv)?;
    build_scoped_request(
        input.execution_space,
        &spec.name,
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
    catalog: &OperationCatalogDocument,
) -> Result<OperationRequest, RequestBuildError> {
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
    catalog: &OperationCatalogDocument,
    request_id: impl Into<String>,
) -> Result<OperationRequest, RequestBuildError> {
    let spec = semantic_operation(input.execution_space, &input.operation, catalog)?;
    let Value::Object(mut values) = input.arguments else {
        return Err(build_error(format!(
            "arguments for {} must be an object",
            input.operation
        )));
    };
    let sandbox_id = if input.execution_space == OperationDomain::Runtime {
        Some(take_runtime_sandbox_id(&mut values)?)
    } else {
        None
    };
    let args = build_args_from_values(spec, values)?;
    build_scoped_request(
        input.execution_space,
        &spec.name,
        args,
        sandbox_id,
        request_id,
    )
}

fn build_scoped_request(
    execution_space: OperationDomain,
    operation: &str,
    args: Value,
    sandbox_id: Option<String>,
    request_id: impl Into<String>,
) -> Result<OperationRequest, RequestBuildError> {
    match execution_space {
        OperationDomain::Manager => Ok(OperationRequest::new(
            operation,
            request_id,
            OperationScope::system(),
            args,
        )),
        OperationDomain::Runtime => Ok(OperationRequest::new(
            operation,
            request_id,
            OperationScope::sandbox(resolve_runtime_sandbox_id(sandbox_id)?),
            args,
        )),
        OperationDomain::Observability => build_observability_request(operation, args, request_id),
    }
}

fn projected_operation<'a>(
    execution_space: OperationDomain,
    operation: &str,
    catalog: &'a CatalogDocument,
) -> Result<(&'a OperationSpecDocument, &'a OperationProjection), RequestBuildError> {
    let spec = semantic_operation(execution_space, operation, &catalog.semantic)?;
    let projection = operation_projection(catalog, operation)
        .ok_or_else(|| build_error(format!("unknown operation: {operation}")))?;
    Ok((spec, projection))
}

fn semantic_operation<'a>(
    execution_space: OperationDomain,
    operation: &str,
    catalog: &'a OperationCatalogDocument,
) -> Result<&'a OperationSpecDocument, RequestBuildError> {
    if execution_space != catalog.operation_execution_space {
        return Err(build_error(format!(
            "loaded catalog is for {}, not {}",
            operation_domain_name(catalog.operation_execution_space),
            operation_domain_name(execution_space)
        )));
    }
    if operation == "help" {
        return Err(build_error(
            "help is reserved and cannot be used as an operation name",
        ));
    }
    catalog
        .operations
        .iter()
        .find(|spec| spec.name == operation)
        .ok_or_else(|| build_error(format!("unknown operation: {operation}")))
}

fn build_observability_request(
    view: &str,
    args: Value,
    request_id: impl Into<String>,
) -> Result<OperationRequest, RequestBuildError> {
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
        return Ok(OperationRequest::new(
            OBSERVABILITY_SNAPSHOT_OP,
            request_id,
            OperationScope::system(),
            Value::Object(args),
        ));
    };
    args.insert("view".to_owned(), Value::String(view.to_owned()));
    Ok(OperationRequest::new(
        OBSERVABILITY_OP,
        request_id,
        OperationScope::sandbox(sandbox_id),
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
    spec: &OperationSpecDocument,
    projection: &OperationProjection,
    argv: &[String],
) -> Result<Value, RequestBuildError> {
    let mut values = Map::new();
    let positional_args = projection
        .arguments
        .iter()
        .filter(|arg| arg.positional.is_some())
        .collect::<Vec<_>>();
    let mut next_positional = 0usize;
    let mut index = 0usize;

    while index < argv.len() {
        let token = &argv[index];
        if token.starts_with("--") {
            let projected_arg = find_flag_arg(projection, token)?;
            let arg = semantic_argument(spec, projected_arg)?;
            index = index.saturating_add(1);
            let value = argv
                .get(index)
                .ok_or_else(|| build_error(format!("{token} requires a value")))?;
            insert_arg_value(&mut values, arg, projected_arg, value)?;
        } else {
            let projected_arg = positional_args.get(next_positional).ok_or_else(|| {
                build_error(format!(
                    "unexpected positional argument for {}: {token}",
                    spec.name
                ))
            })?;
            let arg = semantic_argument(spec, projected_arg)?;
            next_positional = next_positional.saturating_add(1);
            insert_arg_value(&mut values, arg, projected_arg, token)?;
        }
        index = index.saturating_add(1);
    }

    finish_args(spec, values, Some(projection))
}

fn build_args_from_values(
    spec: &OperationSpecDocument,
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
    finish_args(spec, values, None)
}

fn finish_args(
    spec: &OperationSpecDocument,
    mut values: Map<String, Value>,
    projection: Option<&OperationProjection>,
) -> Result<Value, RequestBuildError> {
    for arg in &spec.args {
        if values.contains_key(&arg.name) {
            continue;
        }
        let projected_arg = projection.and_then(|projection| {
            projection
                .arguments
                .iter()
                .find(|candidate| candidate.name == arg.name)
        });
        let name = cli_arg_name(arg, projected_arg);
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
pub fn catalog_arg_default(arg: &ArgSpecDocument) -> Result<Option<Value>, RequestBuildError> {
    arg.default
        .as_deref()
        .map(|default| parse_arg_value(arg, default, &arg.name))
        .transpose()
}

fn insert_arg_value(
    values: &mut Map<String, Value>,
    arg: &ArgSpecDocument,
    projection: &ArgumentProjection,
    value: &str,
) -> Result<(), RequestBuildError> {
    if values.contains_key(&arg.name) {
        return Err(build_error(format!(
            "{} was provided more than once",
            cli_arg_name(arg, Some(projection))
        )));
    }
    values.insert(
        arg.name.clone(),
        parse_arg_value(arg, value, cli_arg_name(arg, Some(projection)))?,
    );
    Ok(())
}

fn parse_arg_value(
    arg: &ArgSpecDocument,
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
    arg: &ArgSpecDocument,
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
    projection: &'a OperationProjection,
    flag: &str,
) -> Result<&'a ArgumentProjection, RequestBuildError> {
    projection
        .arguments
        .iter()
        .find(|arg| arg.accepts_flag(flag))
        .ok_or_else(|| build_error(format!("unknown flag for {}: {flag}", projection.name)))
}

fn semantic_argument<'a>(
    spec: &'a OperationSpecDocument,
    projection: &ArgumentProjection,
) -> Result<&'a ArgSpecDocument, RequestBuildError> {
    spec.args
        .iter()
        .find(|arg| arg.name == projection.name)
        .ok_or_else(|| {
            build_error(format!(
                "unknown argument for {}: {}",
                spec.name, projection.name
            ))
        })
}

fn cli_arg_name<'a>(
    arg: &'a ArgSpecDocument,
    projection: Option<&'a ArgumentProjection>,
) -> &'a str {
    projection
        .and_then(|projection| projection.flag.or(projection.positional))
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
