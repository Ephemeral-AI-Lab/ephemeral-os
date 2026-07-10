use sandbox_operation_client::{catalog_arg_default, RequestBuildError};
use sandbox_operation_contract::{
    ArgKind, ArgSpecDocument, OperationCatalogDocument, OperationSpecDocument,
};
use serde_json::{json, Map, Value};

use crate::config::OperationSet;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolDefinition {
    pub name: String,
    pub description: String,
    pub input_schema: Map<String, Value>,
}

/// Project the selected catalog into MCP tool definitions.
///
/// # Errors
/// Returns an error when a catalog default cannot be represented by its
/// declared argument type.
pub fn tool_definitions(
    set: OperationSet,
    catalog: &OperationCatalogDocument,
) -> Result<Vec<ToolDefinition>, RequestBuildError> {
    catalog
        .operations
        .iter()
        .map(|spec| {
            Ok(ToolDefinition {
                name: spec.name.clone(),
                description: spec.description.clone(),
                input_schema: input_schema(set, spec)?,
            })
        })
        .collect()
}

fn input_schema(
    set: OperationSet,
    spec: &OperationSpecDocument,
) -> Result<Map<String, Value>, RequestBuildError> {
    let mut properties = Map::new();
    let mut required = Vec::new();

    if set == OperationSet::Runtime {
        properties.insert(
            "sandbox_id".to_owned(),
            json!({
                "type": "string",
                "description": "Target sandbox id (selects the daemon to query)."
            }),
        );
        required.push(Value::String("sandbox_id".to_owned()));
    }

    for arg in &spec.args {
        properties.insert(arg.name.clone(), property_schema(arg)?);
        if arg.required {
            required.push(Value::String(arg.name.clone()));
        }
    }

    let mut schema = Map::new();
    schema.insert("type".to_owned(), Value::String("object".to_owned()));
    schema.insert("properties".to_owned(), Value::Object(properties));
    schema.insert("required".to_owned(), Value::Array(required));
    schema.insert("additionalProperties".to_owned(), Value::Bool(false));
    Ok(schema)
}

fn property_schema(arg: &ArgSpecDocument) -> Result<Value, RequestBuildError> {
    let mut property = Map::new();
    match arg.kind {
        ArgKind::String | ArgKind::Path => {
            property.insert("type".to_owned(), Value::String("string".to_owned()));
        }
        ArgKind::Integer => {
            property.insert("type".to_owned(), Value::String("integer".to_owned()));
            property.insert("minimum".to_owned(), Value::from(0));
        }
        ArgKind::Float => {
            property.insert("type".to_owned(), Value::String("number".to_owned()));
        }
        ArgKind::JsonArray => {
            property.insert("type".to_owned(), Value::String("array".to_owned()));
        }
    }
    property.insert("description".to_owned(), Value::String(arg.help.clone()));
    if let Some(default) = catalog_arg_default(arg)? {
        property.insert("default".to_owned(), default);
    }
    Ok(Value::Object(property))
}
