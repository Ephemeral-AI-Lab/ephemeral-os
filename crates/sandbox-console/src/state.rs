//! Shared per-request state: the gateway client and the resolved console
//! configuration every route handler reads.

use sandbox_cli_core::client::GatewayClient;

use crate::config::ConsoleConfig;

#[derive(Debug)]
pub struct AppState {
    pub gateway: GatewayClient,
    pub config: ConsoleConfig,
}

impl AppState {
    #[must_use]
    pub fn new(config: ConsoleConfig) -> Self {
        let gateway = GatewayClient::new(
            config.gateway.gateway_socket_path.to_string_lossy(),
            config.gateway.gateway_auth_token.clone(),
        );
        Self { gateway, config }
    }
}
