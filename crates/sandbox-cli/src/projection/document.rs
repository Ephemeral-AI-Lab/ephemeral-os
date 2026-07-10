use std::collections::HashSet;

use sandbox_operation_contract::document::arg_kind_name;
use sandbox_operation_contract::{
    catalog_from_value as semantic_catalog_from_value,
    catalog_to_value as semantic_catalog_to_value, operation_domain_name, OperationCatalog,
    OperationCatalogDocument, OperationDomain, OperationSpecDocument,
};
use serde_json::{json, Value};

use super::{CatalogProjection, OperationProjection};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogDocument {
    pub semantic: OperationCatalogDocument,
    pub projection: Vec<OperationProjectionDocument>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationProjectionDocument {
    pub name: String,
    pub path: Vec<String>,
    pub usage: String,
    pub examples: Vec<String>,
    pub arguments: Vec<ArgumentProjectionDocument>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgumentProjectionDocument {
    pub name: String,
    pub flag: Option<String>,
    pub additional_flags: Vec<String>,
    pub positional: Option<String>,
}

impl ArgumentProjectionDocument {
    #[must_use]
    pub fn accepts_flag(&self, flag: &str) -> bool {
        self.flag.as_deref() == Some(flag)
            || self
                .additional_flags
                .iter()
                .any(|candidate| candidate == flag)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectionError {
    message: String,
}

impl ProjectionError {
    #[must_use]
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl std::fmt::Display for ProjectionError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for ProjectionError {}

/// Join a semantic catalog with its CLI projection overrides.
///
/// Every public operation receives a projection: an override from `overrides`
/// when one is declared, otherwise a derived projection mapping each argument
/// to a `--kebab-case` flag with a usage line composed from the spec.
///
/// # Errors
/// Returns an error when the semantic catalog is invalid, an override does not
/// match a semantic operation, or a projection binds arguments inconsistently.
pub fn catalog_document(
    catalog: OperationCatalog,
    overrides: CatalogProjection,
) -> Result<CatalogDocument, ProjectionError> {
    let semantic = semantic_catalog_from_value(&semantic_catalog_to_value(catalog))
        .map_err(|error| projection_error(error.message()))?;
    let projection = merged_projection(&semantic, overrides)?;
    Ok(CatalogDocument {
        semantic,
        projection,
    })
}

#[must_use]
pub fn catalog_to_value(document: &CatalogDocument) -> Value {
    json!({
        "operation_execution_space": operation_domain_name(document.semantic.operation_execution_space),
        "families": document.semantic.families.iter().map(|family| json!({
            "id": family.id,
            "title": family.title,
            "summary": family.summary,
            "description": family.description,
        })).collect::<Vec<_>>(),
        "operations": document.semantic.operations.iter().zip(&document.projection).map(|(operation, projection)| {
            operation_to_value(operation, projection)
        }).collect::<Vec<_>>(),
    })
}

#[must_use]
pub fn operation_projection<'a>(
    document: &'a CatalogDocument,
    operation: &str,
) -> Option<&'a OperationProjectionDocument> {
    document
        .projection
        .iter()
        .find(|candidate| candidate.name == operation)
}

#[must_use]
pub fn argument_projection<'a>(
    projection: &'a OperationProjectionDocument,
    argument: &str,
) -> Option<&'a ArgumentProjectionDocument> {
    projection
        .arguments
        .iter()
        .find(|candidate| candidate.name == argument)
}

fn merged_projection(
    semantic: &OperationCatalogDocument,
    overrides: CatalogProjection,
) -> Result<Vec<OperationProjectionDocument>, ProjectionError> {
    if semantic.operation_execution_space != overrides.operation_execution_space {
        return Err(projection_error("catalog and projection domains differ"));
    }

    let mut override_names = HashSet::new();
    for operation in overrides.operations {
        if !override_names.insert(operation.name) {
            return Err(projection_error(format!(
                "duplicate projected operation: {}",
                operation.name
            )));
        }
        if !semantic
            .operations
            .iter()
            .any(|candidate| candidate.name == operation.name)
        {
            return Err(projection_error(format!(
                "projected operation is absent from semantic catalog: {}",
                operation.name
            )));
        }
    }

    let mut merged = Vec::with_capacity(semantic.operations.len());
    let mut paths = HashSet::new();
    for spec in &semantic.operations {
        let projection = overrides
            .operations
            .iter()
            .find(|candidate| candidate.name == spec.name)
            .map_or_else(
                || derived_projection(semantic.operation_execution_space, spec),
                override_document,
            );
        validate_arguments(spec, &projection)?;
        if !paths.insert(projection.path.clone()) {
            return Err(projection_error(format!(
                "duplicate projected path for operation: {}",
                projection.name
            )));
        }
        merged.push(projection);
    }
    Ok(merged)
}

fn override_document(operation: &OperationProjection) -> OperationProjectionDocument {
    OperationProjectionDocument {
        name: operation.name.to_owned(),
        path: operation
            .path
            .iter()
            .map(|part| (*part).to_owned())
            .collect(),
        usage: operation.usage.to_owned(),
        examples: operation
            .examples
            .iter()
            .map(|example| (*example).to_owned())
            .collect(),
        arguments: operation
            .arguments
            .iter()
            .map(|argument| ArgumentProjectionDocument {
                name: argument.name.to_owned(),
                flag: argument.flag.map(str::to_owned),
                additional_flags: argument
                    .additional_flags
                    .iter()
                    .map(|flag| (*flag).to_owned())
                    .collect(),
                positional: argument.positional.map(str::to_owned),
            })
            .collect(),
    }
}

fn derived_projection(
    domain: OperationDomain,
    spec: &OperationSpecDocument,
) -> OperationProjectionDocument {
    OperationProjectionDocument {
        name: spec.name.clone(),
        path: vec![operation_domain_name(domain).to_owned(), spec.name.clone()],
        usage: derived_usage(domain, spec),
        examples: Vec::new(),
        arguments: spec
            .args
            .iter()
            .map(|arg| ArgumentProjectionDocument {
                name: arg.name.clone(),
                flag: Some(derived_flag(&arg.name)),
                additional_flags: Vec::new(),
                positional: None,
            })
            .collect(),
    }
}

fn derived_flag(name: &str) -> String {
    format!("--{}", name.replace('_', "-"))
}

fn derived_usage(domain: OperationDomain, spec: &OperationSpecDocument) -> String {
    let mut usage = format!("{} {}", domain_program(domain), spec.name);
    for arg in &spec.args {
        let token = format!(
            "{} {}",
            derived_flag(&arg.name),
            arg.name.to_ascii_uppercase()
        );
        usage.push(' ');
        if arg.required {
            usage.push_str(&token);
        } else {
            usage.push('[');
            usage.push_str(&token);
            usage.push(']');
        }
    }
    usage
}

const fn domain_program(domain: OperationDomain) -> &'static str {
    match domain {
        OperationDomain::Manager => "sandbox-manager-cli",
        OperationDomain::Runtime => "sandbox-runtime-cli --sandbox-id ID",
        OperationDomain::Observability => "sandbox-observability-cli",
    }
}

fn validate_arguments(
    semantic: &OperationSpecDocument,
    projection: &OperationProjectionDocument,
) -> Result<(), ProjectionError> {
    if semantic.args.len() != projection.arguments.len() {
        return Err(projection_error(format!(
            "semantic and projected argument counts differ for {}",
            projection.name
        )));
    }
    if semantic
        .args
        .iter()
        .zip(&projection.arguments)
        .any(|(semantic, projected)| semantic.name != projected.name)
    {
        return Err(projection_error(format!(
            "semantic and projected argument order differs for {}",
            projection.name
        )));
    }
    let mut argument_names = HashSet::new();
    let mut flags = HashSet::new();
    let mut positionals = HashSet::new();
    for argument in &projection.arguments {
        if !argument_names.insert(argument.name.as_str()) {
            return Err(projection_error(format!(
                "duplicate projected argument for {}: {}",
                projection.name, argument.name
            )));
        }
        if let Some(flag) = argument.flag.as_deref() {
            if !flags.insert(flag) {
                return Err(projection_error(format!(
                    "duplicate flag for {}: {flag}",
                    projection.name
                )));
            }
        }
        for flag in &argument.additional_flags {
            if !flags.insert(flag) {
                return Err(projection_error(format!(
                    "duplicate flag for {}: {flag}",
                    projection.name
                )));
            }
        }
        if let Some(positional) = argument.positional.as_deref() {
            if !positionals.insert(positional) {
                return Err(projection_error(format!(
                    "duplicate positional for {}: {positional}",
                    projection.name
                )));
            }
        }
    }
    Ok(())
}

fn operation_to_value(
    operation: &OperationSpecDocument,
    cli: &OperationProjectionDocument,
) -> Value {
    json!({
        "name": operation.name,
        "family": operation.family,
        "summary": operation.summary,
        "description": operation.description,
        "args": operation.args.iter().zip(&cli.arguments).map(|(argument, cli)| {
            json!({
                "name": argument.name,
                "kind": arg_kind_name(argument.kind),
                "required": argument.required,
                "help": argument.help,
                "default": argument.default,
                "cli": {
                    "flag": cli.flag,
                    "positional": cli.positional,
                },
            })
        }).collect::<Vec<_>>(),
        "cli": {
            "path": cli.path,
            "usage": cli.usage,
            "examples": cli.examples,
        },
        "related": operation.related,
    })
}

fn projection_error(message: impl Into<String>) -> ProjectionError {
    ProjectionError {
        message: message.into(),
    }
}
