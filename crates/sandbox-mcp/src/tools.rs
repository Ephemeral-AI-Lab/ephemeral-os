use sandbox_operation_client::{
    build_request_from_values, BuildRequestValueInput, GatewayClient, GatewayClientError,
};
use sandbox_operation_contract::{
    error_response_with_details, OperationCatalogDocument, OperationRequest, OperationScopeKind,
    OperationScopePolicy, OperationVisibility,
};
use serde_json::{json, Map, Value};

use crate::config::OperationSet;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolOutcome {
    pub value: Value,
    pub is_error: bool,
}

#[derive(Debug)]
pub struct ToolDispatcher {
    set: OperationSet,
    catalog: OperationCatalogDocument,
    client: GatewayClient,
}

impl ToolDispatcher {
    #[must_use]
    pub fn new(
        set: OperationSet,
        catalog: OperationCatalogDocument,
        client: GatewayClient,
    ) -> Self {
        Self {
            set,
            catalog,
            client,
        }
    }

    pub async fn call(&self, name: String, arguments: Option<Map<String, Value>>) -> ToolOutcome {
        let request = match self.build_request(&name, arguments.unwrap_or_default()) {
            Ok(request) => request,
            Err(error) => return error_outcome(error),
        };

        match self.client.send(&request).await {
            Ok(response) if !response.is_object() => error_outcome(error_response_with_details(
                "protocol_error",
                "gateway response must be a JSON object",
                json!({}),
            )),
            Ok(response) => ToolOutcome {
                is_error: response.get("error").is_some(),
                value: response,
            },
            Err(error) => error_outcome(gateway_error(&error)),
        }
    }

    fn build_request(
        &self,
        operation: &str,
        arguments: Map<String, Value>,
    ) -> Result<OperationRequest, Value> {
        if operation == "help" {
            return Err(invalid_request(
                "help is reserved and cannot be used as an operation name",
            ));
        }
        let spec = self
            .catalog
            .operations
            .iter()
            .find(|spec| spec.name == operation)
            .ok_or_else(|| invalid_request(format!("unknown operation: {operation}")))?;
        let scope_policy = self
            .catalog
            .routes
            .iter()
            .find(|route| {
                route.operation == operation && route.visibility == OperationVisibility::Public
            })
            .map(|route| route.scope_policy)
            .ok_or_else(|| invalid_request(format!("unknown operation: {operation}")))?;
        let scope_selector = self.scope_selector(operation, scope_policy, &arguments)?;
        let scope_kind = match scope_policy {
            OperationScopePolicy::System => OperationScopeKind::System,
            OperationScopePolicy::SandboxRequired => OperationScopeKind::Sandbox,
            OperationScopePolicy::SystemOrSandbox if scope_selector.is_some() => {
                OperationScopeKind::Sandbox
            }
            OperationScopePolicy::SystemOrSandbox => OperationScopeKind::System,
        };
        if !self.catalog.routes.iter().any(|route| {
            route.operation == operation
                && route.scope_kind == scope_kind
                && route.visibility == OperationVisibility::Public
        }) {
            return Err(invalid_request(format!("unknown operation: {operation}")));
        }
        build_request_from_values(BuildRequestValueInput {
            spec,
            scope_policy,
            scope_selector,
            arguments: Value::Object(arguments),
        })
        .map_err(|error| error.to_error_envelope())
    }

    fn scope_selector(
        &self,
        operation: &str,
        scope_policy: OperationScopePolicy,
        arguments: &Map<String, Value>,
    ) -> Result<Option<String>, Value> {
        if scope_policy == OperationScopePolicy::System {
            return Ok(None);
        }
        match arguments.get("sandbox_id") {
            Some(Value::String(sandbox_id)) if sandbox_id.trim().is_empty() => {
                let message = if self.set == OperationSet::Observability {
                    "--sandbox-id must be non-empty"
                } else {
                    "sandbox_id must be non-empty"
                };
                Err(invalid_request(message))
            }
            Some(Value::String(sandbox_id)) => Ok(Some(sandbox_id.clone())),
            Some(_) => Err(invalid_request("sandbox_id must be a string")),
            None if scope_policy == OperationScopePolicy::SandboxRequired => {
                let message = if self.set == OperationSet::Observability {
                    format!("sandbox_id is required for {operation}")
                } else {
                    "sandbox_id is required for runtime operations".to_owned()
                };
                Err(invalid_request(message))
            }
            None => Ok(None),
        }
    }
}

fn gateway_error(error: &GatewayClientError) -> Value {
    error_response_with_details(error.kind(), error.to_string(), json!({}))
}

fn invalid_request(message: impl Into<String>) -> Value {
    error_response_with_details("invalid_request", message, json!({}))
}

fn error_outcome(value: Value) -> ToolOutcome {
    ToolOutcome {
        value,
        is_error: true,
    }
}
