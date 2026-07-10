use rmcp::model::{
    CallToolRequestParam, CallToolResult, CompleteRequestMethod, CompleteRequestParam,
    CompleteResult, Implementation, ListPromptsRequestMethod, ListPromptsResult,
    ListResourceTemplatesRequestMethod, ListResourceTemplatesResult, ListResourcesRequestMethod,
    ListResourcesResult, ListToolsResult, PaginatedRequestParam, ProtocolVersion,
    ServerCapabilities, ServerInfo, Tool,
};
use rmcp::service::{RequestContext, RoleServer};
use rmcp::{ErrorData, ServerHandler};
use sandbox_operation_client::{GatewayClient, RequestBuildError};

use crate::schema::{tool_definitions, ToolDefinition};
use crate::tools::ToolDispatcher;

#[derive(Debug)]
pub struct SandboxMcpServer {
    tools: Vec<Tool>,
    dispatcher: ToolDispatcher,
}

impl SandboxMcpServer {
    /// Build the MCP projection of the selected catalog.
    ///
    /// # Errors
    /// Returns an error when a catalog default is invalid.
    pub fn new(
        set: crate::config::OperationSet,
        catalog: sandbox_operation_contract::OperationCatalogDocument,
        client: GatewayClient,
    ) -> Result<Self, RequestBuildError> {
        let tools = tool_definitions(set, &catalog)?
            .into_iter()
            .map(mcp_tool)
            .collect();
        Ok(Self {
            tools,
            dispatcher: ToolDispatcher::new(set, catalog, client),
        })
    }
}

impl ServerHandler for SandboxMcpServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo {
            protocol_version: ProtocolVersion::V_2025_06_18,
            capabilities: ServerCapabilities::builder().enable_tools().build(),
            server_info: Implementation {
                name: "sandbox-mcp".to_owned(),
                title: Some("EphemeralOS Sandbox MCP".to_owned()),
                version: env!("CARGO_PKG_VERSION").to_owned(),
                icons: None,
                website_url: None,
            },
            ..Default::default()
        }
    }

    async fn list_tools(
        &self,
        _request: Option<PaginatedRequestParam>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, ErrorData> {
        Ok(ListToolsResult::with_all_items(self.tools.clone()))
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParam,
        _context: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, ErrorData> {
        let outcome = self
            .dispatcher
            .call(request.name.into_owned(), request.arguments)
            .await;
        Ok(CallToolResult {
            content: Vec::new(),
            structured_content: Some(outcome.value),
            is_error: Some(outcome.is_error),
            meta: None,
        })
    }

    async fn complete(
        &self,
        _request: CompleteRequestParam,
        _context: RequestContext<RoleServer>,
    ) -> Result<CompleteResult, ErrorData> {
        Err(ErrorData::method_not_found::<CompleteRequestMethod>())
    }

    async fn list_prompts(
        &self,
        _request: Option<PaginatedRequestParam>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListPromptsResult, ErrorData> {
        Err(ErrorData::method_not_found::<ListPromptsRequestMethod>())
    }

    async fn list_resources(
        &self,
        _request: Option<PaginatedRequestParam>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListResourcesResult, ErrorData> {
        Err(ErrorData::method_not_found::<ListResourcesRequestMethod>())
    }

    async fn list_resource_templates(
        &self,
        _request: Option<PaginatedRequestParam>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListResourceTemplatesResult, ErrorData> {
        Err(ErrorData::method_not_found::<
            ListResourceTemplatesRequestMethod,
        >())
    }
}

fn mcp_tool(definition: ToolDefinition) -> Tool {
    Tool::new(
        definition.name,
        definition.description,
        definition.input_schema,
    )
}
