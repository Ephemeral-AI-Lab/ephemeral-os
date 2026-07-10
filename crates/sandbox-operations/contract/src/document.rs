use std::collections::{HashMap, HashSet};

use serde_json::{json, Map, Value};

use crate::{
    operation_domain_name, ArgKind, ArgSpec, OperationDomain, OperationExecutionOwner,
    OperationFamilySpec, OperationRouteSpec, OperationScopeKind, OperationScopePolicy,
    OperationSpec, OperationVisibility,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OperationCatalog {
    pub operation_execution_space: OperationDomain,
    pub families: &'static [&'static OperationFamilySpec],
    pub operations: &'static [&'static OperationSpec],
    pub routes: &'static [OperationRouteSpec],
}

impl OperationCatalog {
    #[must_use]
    pub const fn new(
        operation_execution_space: OperationDomain,
        families: &'static [&'static OperationFamilySpec],
        operations: &'static [&'static OperationSpec],
        routes: &'static [OperationRouteSpec],
    ) -> Self {
        Self {
            operation_execution_space,
            families,
            operations,
            routes,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationCatalogDocument {
    pub operation_execution_space: OperationDomain,
    pub families: Vec<OperationFamilyDocument>,
    pub operations: Vec<OperationSpecDocument>,
    pub routes: Vec<OperationRouteDocument>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationFamilyDocument {
    pub id: String,
    pub title: String,
    pub summary: String,
    pub description: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationSpecDocument {
    pub name: String,
    pub family: String,
    pub summary: String,
    pub description: String,
    pub args: Vec<ArgSpecDocument>,
    pub related: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationRouteDocument {
    pub operation: String,
    pub scope_policy: OperationScopePolicy,
    pub scope_kind: OperationScopeKind,
    pub execution_owner: OperationExecutionOwner,
    pub visibility: OperationVisibility,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgSpecDocument {
    pub name: String,
    pub kind: ArgKind,
    pub required: bool,
    pub help: String,
    pub default: Option<String>,
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
        "operation_execution_space": operation_domain_name(catalog.operation_execution_space),
        "families": catalog
            .families
            .iter()
            .map(|family| operation_family_value(family))
            .collect::<Vec<_>>(),
        "operations": catalog
            .operations
            .iter()
            .map(|spec| operation_spec_value(spec))
            .collect::<Vec<_>>(),
        "routes": catalog
            .routes
            .iter()
            .map(operation_route_value)
            .collect::<Vec<_>>(),
    })
}

pub fn catalog_from_value(value: &Value) -> Result<OperationCatalogDocument, CatalogDecodeError> {
    let object = value
        .as_object()
        .ok_or_else(|| decode_error("operation catalog response must be an object"))?;
    let operation_execution_space =
        operation_domain_from_name(required_string(object, "operation_execution_space")?)?;
    let families = required_array(object, "families")?
        .iter()
        .map(operation_family_from_value)
        .collect::<Result<Vec<_>, _>>()?;
    let operations = required_array(object, "operations")?
        .iter()
        .map(operation_spec_from_value)
        .collect::<Result<Vec<_>, _>>()?;
    let routes = required_array(object, "routes")?
        .iter()
        .map(operation_route_from_value)
        .collect::<Result<Vec<_>, _>>()?;
    validate_catalog(&families, &operations, &routes)?;
    Ok(OperationCatalogDocument {
        operation_execution_space,
        families,
        operations,
        routes,
    })
}

#[must_use]
pub const fn arg_kind_name(kind: ArgKind) -> &'static str {
    match kind {
        ArgKind::String => "string",
        ArgKind::Integer => "integer",
        ArgKind::Float => "float",
        ArgKind::Path => "path",
        ArgKind::JsonArray => "json_array",
    }
}

#[must_use]
pub const fn operation_scope_policy_name(policy: OperationScopePolicy) -> &'static str {
    match policy {
        OperationScopePolicy::System => "system",
        OperationScopePolicy::SandboxRequired => "sandbox_required",
        OperationScopePolicy::SystemOrSandbox => "system_or_sandbox",
    }
}

#[must_use]
pub const fn operation_scope_kind_name(kind: OperationScopeKind) -> &'static str {
    match kind {
        OperationScopeKind::System => "system",
        OperationScopeKind::Sandbox => "sandbox",
    }
}

#[must_use]
pub const fn operation_execution_owner_name(owner: OperationExecutionOwner) -> &'static str {
    match owner {
        OperationExecutionOwner::Manager => "manager",
        OperationExecutionOwner::Runtime => "runtime",
        OperationExecutionOwner::Observability => "observability",
    }
}

#[must_use]
pub const fn operation_visibility_name(visibility: OperationVisibility) -> &'static str {
    match visibility {
        OperationVisibility::Public => "public",
        OperationVisibility::Internal => "internal",
    }
}

fn operation_spec_value(spec: &OperationSpec) -> Value {
    json!({
        "name": spec.name,
        "family": spec.family,
        "summary": spec.summary,
        "description": spec.description,
        "args": spec.args.iter().map(arg_spec_value).collect::<Vec<_>>(),
        "related": spec.related,
    })
}

fn operation_route_value(route: &OperationRouteSpec) -> Value {
    json!({
        "operation": route.operation,
        "scope_policy": operation_scope_policy_name(route.scope_policy),
        "scope_kind": operation_scope_kind_name(route.scope_kind),
        "execution_owner": operation_execution_owner_name(route.execution_owner),
        "visibility": operation_visibility_name(route.visibility),
    })
}

fn operation_family_value(spec: &OperationFamilySpec) -> Value {
    json!({
        "id": spec.id,
        "title": spec.title,
        "summary": spec.summary,
        "description": spec.description,
    })
}

fn arg_spec_value(spec: &ArgSpec) -> Value {
    json!({
        "name": spec.name,
        "kind": arg_kind_name(spec.kind),
        "required": spec.required,
        "help": spec.help,
        "default": spec.default,
    })
}

fn operation_family_from_value(
    value: &Value,
) -> Result<OperationFamilyDocument, CatalogDecodeError> {
    let object = value
        .as_object()
        .ok_or_else(|| decode_error("operation family spec must be an object"))?;
    Ok(OperationFamilyDocument {
        id: required_string(object, "id")?.to_owned(),
        title: required_string(object, "title")?.to_owned(),
        summary: required_string(object, "summary")?.to_owned(),
        description: required_string(object, "description")?.to_owned(),
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
    let related = required_string_array(object, "related", "related operation entries")?;
    Ok(OperationSpecDocument {
        name: required_string(object, "name")?.to_owned(),
        family: required_string(object, "family")?.to_owned(),
        summary: required_string(object, "summary")?.to_owned(),
        description: required_string(object, "description")?.to_owned(),
        args,
        related,
    })
}

fn operation_route_from_value(value: &Value) -> Result<OperationRouteDocument, CatalogDecodeError> {
    let object = value
        .as_object()
        .ok_or_else(|| decode_error("operation route must be an object"))?;
    Ok(OperationRouteDocument {
        operation: required_string(object, "operation")?.to_owned(),
        scope_policy: operation_scope_policy_from_name(required_string(object, "scope_policy")?)?,
        scope_kind: operation_scope_kind_from_name(required_string(object, "scope_kind")?)?,
        execution_owner: operation_execution_owner_from_name(required_string(
            object,
            "execution_owner",
        )?)?,
        visibility: operation_visibility_from_name(required_string(object, "visibility")?)?,
    })
}

fn validate_catalog(
    families: &[OperationFamilyDocument],
    operations: &[OperationSpecDocument],
    routes: &[OperationRouteDocument],
) -> Result<(), CatalogDecodeError> {
    let mut family_ids = HashSet::new();
    for family in families {
        if !family_ids.insert(family.id.as_str()) {
            return Err(decode_error(format!(
                "duplicate operation family id: {}",
                family.id
            )));
        }
    }

    let mut operation_names = HashSet::new();
    for operation in operations {
        if !family_ids.contains(operation.family.as_str()) {
            return Err(decode_error(format!(
                "operation {} references unknown family: {}",
                operation.name, operation.family
            )));
        }
        if !operation_names.insert(operation.name.as_str()) {
            return Err(decode_error(format!(
                "duplicate operation name: {}",
                operation.name
            )));
        }
    }

    for operation in operations {
        for related in &operation.related {
            if !operation_names.contains(related.as_str()) {
                return Err(decode_error(format!(
                    "operation {} references unknown related operation: {}",
                    operation.name, related
                )));
            }
        }
    }

    let mut route_keys = HashSet::new();
    let mut routed_operations = HashSet::new();
    let mut route_groups = HashMap::new();
    for route in routes {
        if !operation_names.contains(route.operation.as_str()) {
            return Err(decode_error(format!(
                "route references unknown operation: {}",
                route.operation
            )));
        }
        if route.visibility != OperationVisibility::Public {
            return Err(decode_error(format!(
                "semantic catalog route must be public: {}",
                route.operation
            )));
        }
        if matches!(
            (route.scope_policy, route.scope_kind),
            (OperationScopePolicy::System, OperationScopeKind::Sandbox)
                | (
                    OperationScopePolicy::SandboxRequired,
                    OperationScopeKind::System
                )
        ) {
            return Err(decode_error(format!(
                "route scope policy and kind differ for operation: {}",
                route.operation
            )));
        }
        let key = (
            route.operation.as_str(),
            operation_scope_kind_name(route.scope_kind),
        );
        if !route_keys.insert(key) {
            return Err(decode_error(format!(
                "duplicate operation route: {} ({})",
                route.operation,
                operation_scope_kind_name(route.scope_kind)
            )));
        }
        let (policy, kinds) = route_groups
            .entry(route.operation.as_str())
            .or_insert_with(|| (route.scope_policy, Vec::new()));
        if *policy != route.scope_policy {
            return Err(decode_error(format!(
                "operation routes use mixed scope policies: {}",
                route.operation
            )));
        }
        kinds.push(route.scope_kind);
        routed_operations.insert(route.operation.as_str());
    }

    for (operation, (policy, kinds)) in route_groups {
        let valid = match policy {
            OperationScopePolicy::System => kinds.as_slice() == [OperationScopeKind::System],
            OperationScopePolicy::SandboxRequired => {
                kinds.as_slice() == [OperationScopeKind::Sandbox]
            }
            OperationScopePolicy::SystemOrSandbox => {
                kinds.len() == 2
                    && kinds.contains(&OperationScopeKind::System)
                    && kinds.contains(&OperationScopeKind::Sandbox)
            }
        };
        if !valid {
            return Err(decode_error(format!(
                "operation route expansion does not match scope policy: {operation}"
            )));
        }
    }

    for operation in operations {
        if !routed_operations.contains(operation.name.as_str()) {
            return Err(decode_error(format!(
                "operation has no public route: {}",
                operation.name
            )));
        }
    }

    Ok(())
}

fn operation_scope_policy_from_name(
    value: &str,
) -> Result<OperationScopePolicy, CatalogDecodeError> {
    match value {
        "system" => Ok(OperationScopePolicy::System),
        "sandbox_required" => Ok(OperationScopePolicy::SandboxRequired),
        "system_or_sandbox" => Ok(OperationScopePolicy::SystemOrSandbox),
        other => Err(decode_error(format!("unknown route scope policy: {other}"))),
    }
}

fn operation_scope_kind_from_name(value: &str) -> Result<OperationScopeKind, CatalogDecodeError> {
    match value {
        "system" => Ok(OperationScopeKind::System),
        "sandbox" => Ok(OperationScopeKind::Sandbox),
        other => Err(decode_error(format!("unknown route scope kind: {other}"))),
    }
}

fn operation_execution_owner_from_name(
    value: &str,
) -> Result<OperationExecutionOwner, CatalogDecodeError> {
    match value {
        "manager" => Ok(OperationExecutionOwner::Manager),
        "runtime" => Ok(OperationExecutionOwner::Runtime),
        "observability" => Ok(OperationExecutionOwner::Observability),
        other => Err(decode_error(format!(
            "unknown route execution owner: {other}"
        ))),
    }
}

fn operation_visibility_from_name(value: &str) -> Result<OperationVisibility, CatalogDecodeError> {
    match value {
        "public" => Ok(OperationVisibility::Public),
        "internal" => Ok(OperationVisibility::Internal),
        other => Err(decode_error(format!("unknown route visibility: {other}"))),
    }
}

fn arg_spec_from_value(value: &Value) -> Result<ArgSpecDocument, CatalogDecodeError> {
    let object = value
        .as_object()
        .ok_or_else(|| decode_error("operation arg spec must be an object"))?;
    Ok(ArgSpecDocument {
        name: required_string(object, "name")?.to_owned(),
        kind: arg_kind_from_name(required_string(object, "kind")?)?,
        required: required_bool(object, "required")?,
        help: required_string(object, "help")?.to_owned(),
        default: optional_string(object, "default")?.map(str::to_owned),
    })
}

fn operation_domain_from_name(value: &str) -> Result<OperationDomain, CatalogDecodeError> {
    match value {
        "manager" => Ok(OperationDomain::Manager),
        "runtime" => Ok(OperationDomain::Runtime),
        "observability" => Ok(OperationDomain::Observability),
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
        "json_array" => Ok(ArgKind::JsonArray),
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
