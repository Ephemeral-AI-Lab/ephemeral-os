use sandbox_operation_contract::{
    error, error_response_with_details, ArgKind, ArgSpecDocument, OperationRequest, OperationScope,
    OperationScopePolicy, OperationSpecDocument,
};
use serde_json::{Map, Number, Value};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuildRequestValueInput<'a> {
    pub spec: &'a OperationSpecDocument,
    pub scope_policy: OperationScopePolicy,
    pub scope_selector: Option<String>,
    pub arguments: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestBuildError {
    message: String,
}

impl RequestBuildError {
    #[must_use]
    pub fn invalid(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }

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

/// Build a request from typed argument values and a resolved semantic spec.
///
/// # Errors
/// Returns an error for invalid arguments or a scope selector that conflicts
/// with the supplied scope policy.
pub fn build_request_from_values(
    input: BuildRequestValueInput<'_>,
) -> Result<OperationRequest, RequestBuildError> {
    build_request_from_values_with_id(input, next_request_id())
}

/// Build a request with an explicit request id from a resolved semantic spec.
///
/// # Errors
/// Returns an error for invalid arguments or a scope selector that conflicts
/// with the supplied scope policy.
pub fn build_request_from_values_with_id(
    input: BuildRequestValueInput<'_>,
    request_id: impl Into<String>,
) -> Result<OperationRequest, RequestBuildError> {
    let scope = build_scope(
        input.spec,
        input.scope_policy,
        input.scope_selector.as_deref(),
    )?;
    let Value::Object(mut supplied) = input.arguments else {
        return Err(build_error(format!(
            "arguments for {} must be an object",
            input.spec.name
        )));
    };
    let selector_is_scope = input.scope_policy != OperationScopePolicy::System;
    if selector_is_scope {
        remove_scope_selector_copy(&mut supplied, input.scope_selector.as_deref())?;
    }
    let args = build_args_from_values(input.spec, supplied, selector_is_scope)?;
    Ok(OperationRequest::new(
        &input.spec.name,
        request_id,
        scope,
        args,
    ))
}

/// Parse a declared semantic default into its native JSON value.
///
/// # Errors
/// Returns an error when the default does not match its declared kind.
pub fn catalog_arg_default(arg: &ArgSpecDocument) -> Result<Option<Value>, RequestBuildError> {
    arg.default
        .as_deref()
        .map(|default| parse_arg_default(arg, default))
        .transpose()
}

fn build_scope(
    spec: &OperationSpecDocument,
    policy: OperationScopePolicy,
    selector: Option<&str>,
) -> Result<OperationScope, RequestBuildError> {
    match (policy, selector) {
        (OperationScopePolicy::System, None) | (OperationScopePolicy::SystemOrSandbox, None) => {
            Ok(OperationScope::system())
        }
        (OperationScopePolicy::System, Some(_)) => Err(build_error(format!(
            "system-scoped operation {} does not accept a scope selector",
            spec.name
        ))),
        (OperationScopePolicy::SandboxRequired, None) => Err(build_error(format!(
            "scope selector is required for {}",
            spec.name
        ))),
        (
            OperationScopePolicy::SandboxRequired | OperationScopePolicy::SystemOrSandbox,
            Some(id),
        ) => {
            if id.trim().is_empty() {
                Err(build_error("sandbox_id must be non-empty"))
            } else {
                Ok(OperationScope::sandbox(id))
            }
        }
    }
}

fn remove_scope_selector_copy(
    supplied: &mut Map<String, Value>,
    selector: Option<&str>,
) -> Result<(), RequestBuildError> {
    let Some(copy) = supplied.remove("sandbox_id") else {
        return Ok(());
    };
    let Some(copy) = copy.as_str() else {
        return Err(build_error("sandbox_id must be a string"));
    };
    if copy.trim().is_empty() {
        return Err(build_error("sandbox_id must be non-empty"));
    }
    if selector != Some(copy) {
        return Err(build_error("sandbox_id must match the scope selector"));
    }
    Ok(())
}

fn build_args_from_values(
    spec: &OperationSpecDocument,
    mut supplied: Map<String, Value>,
    selector_is_scope: bool,
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
        if selector_is_scope && arg.name == "sandbox_id" {
            continue;
        }
        if let Some(value) = supplied.remove(&arg.name) {
            values.insert(arg.name.clone(), validate_arg_value(arg, value)?);
        }
    }
    finish_args(spec, values, selector_is_scope)
}

fn finish_args(
    spec: &OperationSpecDocument,
    mut values: Map<String, Value>,
    selector_is_scope: bool,
) -> Result<Value, RequestBuildError> {
    for arg in &spec.args {
        if (selector_is_scope && arg.name == "sandbox_id") || values.contains_key(&arg.name) {
            continue;
        }
        if let Some(default) = catalog_arg_default(arg)? {
            values.insert(arg.name.clone(), default);
        } else if arg.required {
            return Err(build_error(format!(
                "{} is required for {}",
                arg.name, spec.name
            )));
        }
    }
    Ok(Value::Object(values))
}

fn parse_arg_default(arg: &ArgSpecDocument, value: &str) -> Result<Value, RequestBuildError> {
    let value = match arg.kind {
        ArgKind::String | ArgKind::Path => Ok(Value::String(value.to_owned())),
        ArgKind::Integer => value.parse::<u64>().map_or_else(
            |_| {
                Err(build_error(format!(
                    "{} must be an unsigned integer",
                    arg.name
                )))
            },
            |number| Ok(Value::Number(Number::from(number))),
        ),
        ArgKind::Float => {
            let parsed = value
                .parse::<f64>()
                .map_err(|_| build_error(format!("{} must be a finite number", arg.name)))?;
            Number::from_f64(parsed)
                .map(Value::Number)
                .ok_or_else(|| build_error(format!("{} must be finite", arg.name)))
        }
        ArgKind::JsonArray => serde_json::from_str::<Value>(value)
            .map_err(|_| build_error(format!("{} must be a JSON array", arg.name))),
    }?;
    validate_arg_value(arg, value)
}

fn validate_arg_value(arg: &ArgSpecDocument, value: Value) -> Result<Value, RequestBuildError> {
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
    Err(build_error(format!("{} must be {expected}", arg.name)))
}

fn next_request_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

fn build_error(message: impl Into<String>) -> RequestBuildError {
    RequestBuildError::invalid(message)
}
