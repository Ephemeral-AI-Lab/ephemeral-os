//! Runtime binding for catalog plugin tools.
//!
//! `eos-plugin-catalog` owns the declared model-facing specs. This module binds
//! those specs into real `eos-tools` executors that dispatch dynamic
//! `plugin.<plugin>.<op>` daemon operations. It deliberately does not run Python
//! setup/install code; plugin runtimes are external payload processes behind the
//! Rust daemon PPC route.

use std::sync::Arc;

use async_trait::async_trait;
use eos_llm_client::ToolSpec;
use eos_plugin_catalog::{plugin_tool_specs, PluginToolSpec};
use eos_sandbox_api::{Intent, PluginDispatchRequest, SandboxRequestBase};
use eos_tools::{
    ExecutionMetadata, OutputShape, RegisteredTool, ToolError, ToolExecutor, ToolIntent, ToolKey,
    ToolRegistry, ToolResult,
};
use eos_types::JsonObject;
use serde_json::Value;

const PLUGIN_DISPATCH_TIMEOUT_S: u32 = 150;

/// Register every built-in plugin catalog tool into `registry`.
pub(crate) fn register_plugin_tools(registry: &mut ToolRegistry) {
    for spec in plugin_tool_specs() {
        registry.register(registered_plugin_tool(spec));
    }
}

fn registered_plugin_tool(spec: PluginToolSpec) -> RegisteredTool {
    let name = spec.name.as_str().to_owned();
    let parsed_name = split_plugin_tool_name(&name);
    let input_schema = match serde_json::to_value(spec.input_schema) {
        Ok(Value::Object(map)) => map,
        _ => JsonObject::new(),
    };
    let tool_spec = ToolSpec::new(name.clone(), spec.description, input_schema, None);
    RegisteredTool::new(
        ToolKey::dynamic(name),
        ToolIntent::from(spec.intent),
        false,
        tool_spec,
        OutputShape::Text,
        Arc::new(PluginToolExecutor {
            parsed_name,
            intent: spec.intent,
        }),
    )
}

fn split_plugin_tool_name(name: &str) -> Option<(String, String)> {
    name.split_once('.')
        .filter(|(plugin_id, op_name)| !plugin_id.is_empty() && !op_name.is_empty())
        .map(|(plugin_id, op_name)| (plugin_id.to_owned(), op_name.to_owned()))
}

#[derive(Debug)]
struct PluginToolExecutor {
    parsed_name: Option<(String, String)>,
    intent: Intent,
}

#[async_trait]
impl ToolExecutor for PluginToolExecutor {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let Some((plugin_id, op_name)) = &self.parsed_name else {
            return Err(ToolError::Internal(
                "catalog plugin tool name must be <plugin>.<op>".to_owned(),
            ));
        };
        let sandbox_id = ctx.require_sandbox_id()?;
        let base = SandboxRequestBase {
            caller: ctx.caller.clone(),
            description: format!("plugin {plugin_id}.{op_name}"),
            invocation_id: ctx.sandbox_invocation_id.clone(),
        };
        let response = eos_sandbox_api::plugin_dispatch(
            &*ctx.transport,
            sandbox_id,
            PluginDispatchRequest {
                base,
                plugin_id: plugin_id.clone(),
                op_name: op_name.clone(),
                intent: self.intent,
                workspace_root: ctx.repo_root.clone(),
                args: input.clone(),
                timeout_s: PLUGIN_DISPATCH_TIMEOUT_S,
            },
        )
        .await?;
        Ok(plugin_result(response))
    }
}

fn plugin_result(response: JsonObject) -> ToolResult {
    let is_error = response.get("success") == Some(&Value::Bool(false));
    let output = serde_json::to_string(&response)
        .unwrap_or_else(|err| format!(r#"{{"success":false,"error":"{err}"}}"#));
    if is_error {
        ToolResult::error(output)
    } else {
        ToolResult::ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registers_lsp_plugin_tools() {
        let mut registry = ToolRegistry::new();
        register_plugin_tools(&mut registry);
        let hover = registry.get_wire("lsp.hover").expect("hover registered");
        assert_eq!(hover.name.as_str(), "lsp.hover");
        assert_eq!(hover.intent, ToolIntent::ReadOnly);
        assert!(!hover.is_terminal);
        assert!(registry.get_wire("lsp.rename").is_some());
    }
}
