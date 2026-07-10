use std::collections::HashSet;

use sandbox_operation_contract::document::arg_kind_name;
use sandbox_operation_contract::{
    catalog_from_value as semantic_catalog_from_value,
    catalog_to_value as semantic_catalog_to_value, operation_domain_name, OperationCatalog,
    OperationCatalogDocument, OperationSpecDocument,
};
use serde_json::{json, Value};

use super::{ArgumentProjection, CatalogProjection, OperationProjection};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogDocument {
    pub semantic: OperationCatalogDocument,
    pub projection: CatalogProjection,
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

pub fn catalog_document(
    catalog: OperationCatalog,
    projection: CatalogProjection,
) -> Result<CatalogDocument, ProjectionError> {
    let semantic = semantic_catalog_from_value(&semantic_catalog_to_value(catalog))
        .map_err(|error| projection_error(error.message()))?;
    validate_projection(&semantic, projection)?;
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
        "operations": document.semantic.operations.iter().zip(document.projection.operations).map(|(operation, projection)| {
            operation_to_value(operation, projection)
        }).collect::<Vec<_>>(),
    })
}

#[must_use]
pub fn operation_projection<'a>(
    document: &'a CatalogDocument,
    operation: &str,
) -> Option<&'a OperationProjection> {
    document
        .projection
        .operations
        .iter()
        .find(|candidate| candidate.name == operation)
}

#[must_use]
pub fn argument_projection<'a>(
    projection: &'a OperationProjection,
    argument: &str,
) -> Option<&'a ArgumentProjection> {
    projection
        .arguments
        .iter()
        .find(|candidate| candidate.name == argument)
}

fn validate_projection(
    semantic: &OperationCatalogDocument,
    projection: CatalogProjection,
) -> Result<(), ProjectionError> {
    if semantic.operation_execution_space != projection.operation_execution_space {
        return Err(projection_error("catalog and projection domains differ"));
    }
    if semantic.operations.len() != projection.operations.len() {
        return Err(projection_error(
            "semantic catalog and CLI projection operation counts differ",
        ));
    }
    if semantic
        .operations
        .iter()
        .zip(projection.operations)
        .any(|(semantic, projected)| semantic.name != projected.name)
    {
        return Err(projection_error(
            "semantic catalog and CLI projection operation order differs",
        ));
    }

    let mut operation_names = HashSet::new();
    let mut paths = HashSet::new();
    for operation in projection.operations {
        if !operation_names.insert(operation.name) {
            return Err(projection_error(format!(
                "duplicate projected operation: {}",
                operation.name
            )));
        }
        if !paths.insert(operation.path) {
            return Err(projection_error(format!(
                "duplicate projected path for operation: {}",
                operation.name
            )));
        }
        let semantic_operation = semantic
            .operations
            .iter()
            .find(|candidate| candidate.name == operation.name)
            .ok_or_else(|| {
                projection_error(format!(
                    "projected operation is absent from semantic catalog: {}",
                    operation.name
                ))
            })?;
        validate_arguments(semantic_operation, operation)?;
    }

    for operation in &semantic.operations {
        if !operation_names.contains(operation.name.as_str()) {
            return Err(projection_error(format!(
                "semantic operation has no CLI projection: {}",
                operation.name
            )));
        }
    }

    Ok(())
}

fn validate_arguments(
    semantic: &OperationSpecDocument,
    projection: &OperationProjection,
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
        .zip(projection.arguments)
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
    for argument in projection.arguments {
        if !argument_names.insert(argument.name) {
            return Err(projection_error(format!(
                "duplicate projected argument for {}: {}",
                projection.name, argument.name
            )));
        }
        if !semantic
            .args
            .iter()
            .any(|candidate| candidate.name == argument.name)
        {
            return Err(projection_error(format!(
                "projected argument is absent from {}: {}",
                projection.name, argument.name
            )));
        }
        if let Some(flag) = argument.flag {
            if !flags.insert(flag) {
                return Err(projection_error(format!(
                    "duplicate flag for {}: {flag}",
                    projection.name
                )));
            }
        }
        for flag in argument.additional_flags {
            if !flags.insert(flag) {
                return Err(projection_error(format!(
                    "duplicate flag for {}: {flag}",
                    projection.name
                )));
            }
        }
        if let Some(positional) = argument.positional {
            if !positionals.insert(positional) {
                return Err(projection_error(format!(
                    "duplicate positional for {}: {positional}",
                    projection.name
                )));
            }
        }
    }

    for argument in &semantic.args {
        if !argument_names.contains(argument.name.as_str()) {
            return Err(projection_error(format!(
                "semantic argument has no CLI projection for {}: {}",
                projection.name, argument.name
            )));
        }
    }

    Ok(())
}

fn operation_to_value(operation: &OperationSpecDocument, cli: &OperationProjection) -> Value {
    json!({
        "name": operation.name,
        "family": operation.family,
        "summary": operation.summary,
        "description": operation.description,
        "args": operation.args.iter().zip(cli.arguments).map(|(argument, cli)| {
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
