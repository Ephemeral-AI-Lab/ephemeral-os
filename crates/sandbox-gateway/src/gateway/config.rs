use std::path::PathBuf;

pub const DEFAULT_GATEWAY_SOCKET: &str = "/tmp/sandbox-gateway.sock";
pub const DEFAULT_GATEWAY_PID: &str = "/tmp/sandbox-gateway.pid";
pub const DEFAULT_MAX_CONCURRENT_CONNECTIONS: usize = 256;
pub const SANDBOX_GATEWAY_SOCKET_ENV: &str = "SANDBOX_GATEWAY_SOCKET";

#[derive(Debug, Clone)]
pub struct GatewayConfig {
    pub socket_path: PathBuf,
    pub pid_path: PathBuf,
    pub max_concurrent_connections: usize,
}

impl GatewayConfig {
    #[must_use]
    pub fn new(
        socket_path: impl Into<PathBuf>,
        pid_path: impl Into<PathBuf>,
        max_concurrent_connections: usize,
    ) -> Self {
        Self {
            socket_path: socket_path.into(),
            pid_path: pid_path.into(),
            max_concurrent_connections,
        }
    }
}
