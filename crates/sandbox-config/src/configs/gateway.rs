//! Config for the public sandbox gateway server.

use std::path::PathBuf;

pub const DEFAULT_GATEWAY_SOCKET: &str = "127.0.0.1:7878";
pub const DEFAULT_GATEWAY_PID: &str = "/tmp/eos-gateway.pid";
pub const DEFAULT_MAX_CONCURRENT_CONNECTIONS: usize = 256;
pub const SANDBOX_GATEWAY_SOCKET_ENV: &str = "SANDBOX_GATEWAY_SOCKET";
pub const SANDBOX_GATEWAY_AUTH_TOKEN_ENV: &str = "SANDBOX_GATEWAY_AUTH_TOKEN";

#[derive(Debug, Clone)]
pub struct GatewayConfig {
    pub bind_addr: String,
    pub pid_path: PathBuf,
    pub max_concurrent_connections: usize,
    pub auth_token: Option<String>,
}

impl GatewayConfig {
    #[must_use]
    pub fn new(
        bind_addr: impl Into<String>,
        pid_path: impl Into<PathBuf>,
        max_concurrent_connections: usize,
        auth_token: Option<String>,
    ) -> Self {
        Self {
            bind_addr: bind_addr.into(),
            pid_path: pid_path.into(),
            max_concurrent_connections,
            auth_token,
        }
    }
}
