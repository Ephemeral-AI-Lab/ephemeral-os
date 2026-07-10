use std::path::PathBuf;

use clap::{Parser, ValueEnum};
use sandbox_cli::core::{GatewayConfig, GatewayConfigOverrides};
use sandbox_protocol::CliOperationExecutionSpace;

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum OperationSet {
    Management,
    Runtime,
    Observability,
}

impl OperationSet {
    #[must_use]
    pub const fn execution_space(self) -> CliOperationExecutionSpace {
        match self {
            Self::Management => CliOperationExecutionSpace::Manager,
            Self::Runtime => CliOperationExecutionSpace::Runtime,
            Self::Observability => CliOperationExecutionSpace::Observability,
        }
    }
}

#[derive(Debug, Parser)]
#[command(name = "sandbox-mcp")]
pub struct Cli {
    #[arg(long, value_enum)]
    pub set: OperationSet,

    #[arg(long = "gateway-socket", value_name = "HOST:PORT")]
    gateway_socket_path: Option<PathBuf>,

    #[arg(long = "gateway-auth-token", value_name = "TOKEN")]
    gateway_auth_token: Option<String>,
}

impl Cli {
    /// Resolve explicit gateway overrides through the shared CLI config path.
    ///
    /// # Errors
    /// Returns an error for an empty gateway address or authentication token.
    pub fn discover_gateway(&self) -> Result<GatewayConfig, sandbox_cli::core::ConfigError> {
        GatewayConfig::discover(GatewayConfigOverrides {
            gateway_socket_path: self.gateway_socket_path.clone(),
            gateway_auth_token: self.gateway_auth_token.clone(),
        })
    }
}
