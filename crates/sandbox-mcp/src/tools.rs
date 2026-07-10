use sandbox_cli::core::client::{GatewayClient, GatewayClientError};
use sandbox_cli::core::request_builder::{build_request_from_values, BuildRequestValueInput};
use sandbox_protocol::{error_response_with_details, CliOperationCatalogDocument};
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
    catalog: CliOperationCatalogDocument,
    client: GatewayClient,
}

impl ToolDispatcher {
    #[must_use]
    pub fn new(
        set: OperationSet,
        catalog: CliOperationCatalogDocument,
        client: GatewayClient,
    ) -> Self {
        Self {
            set,
            catalog,
            client,
        }
    }

    pub async fn call(&self, name: String, arguments: Option<Map<String, Value>>) -> ToolOutcome {
        let request = match build_request_from_values(
            BuildRequestValueInput {
                execution_space: self.set.execution_space(),
                operation: name,
                arguments: Value::Object(arguments.unwrap_or_default()),
            },
            &self.catalog,
        ) {
            Ok(request) => request,
            Err(error) => return error_outcome(error.to_error_envelope()),
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
}

fn gateway_error(error: &GatewayClientError) -> Value {
    error_response_with_details(error.kind(), error.to_string(), json!({}))
}

fn error_outcome(value: Value) -> ToolOutcome {
    ToolOutcome {
        value,
        is_error: true,
    }
}
