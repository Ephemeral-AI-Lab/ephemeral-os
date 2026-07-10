#![forbid(unsafe_code)]

pub mod catalog;
pub mod config;
mod schema;
mod server;
mod tools;

use rmcp::ServiceExt;
use sandbox_cli::core::client::GatewayClient;
use sandbox_cli::core::GatewayConfig;

use crate::catalog::selected_catalog;
use crate::config::OperationSet;
use crate::server::SandboxMcpServer;

/// Start one fixed-set MCP server over stdin/stdout and run until the client
/// closes the transport.
///
/// # Errors
/// Returns an error when catalog projection, MCP startup, or the service task
/// fails.
pub async fn run(
    set: OperationSet,
    gateway: GatewayConfig,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let catalog = selected_catalog(set)?;
    let client = GatewayClient::new(
        gateway.gateway_socket_path.to_string_lossy().into_owned(),
        gateway.gateway_auth_token,
    );
    let server = SandboxMcpServer::new(set, catalog, client)?;
    let service = server.serve(rmcp::transport::stdio()).await?;
    service.waiting().await?;
    Ok(())
}
