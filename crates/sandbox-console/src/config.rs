//! Console configuration: loopback bind address, gateway endpoint and auth
//! token (via the shared CLI discovery), and the SPA asset directory.

use std::path::PathBuf;
use std::time::Duration;

use sandbox_cli::core::{ConfigError, GatewayConfig, GatewayConfigOverrides};

pub const SANDBOX_CONSOLE_BIND_ENV: &str = "SANDBOX_CONSOLE_BIND";
pub const SANDBOX_CONSOLE_ASSETS_ENV: &str = "SANDBOX_CONSOLE_ASSETS";
pub const DEFAULT_CONSOLE_BIND: &str = "127.0.0.1:7880";
pub const DEFAULT_RPC_TIMEOUT: Duration = Duration::from_secs(120);

const DEFAULT_ASSET_DIRS: &[&str] = &["dist/console", "web/console/dist"];

#[derive(Debug, Clone)]
pub struct ConsoleConfig {
    pub bind: String,
    pub gateway: GatewayConfig,
    pub assets_dir: Option<PathBuf>,
    pub rpc_timeout: Duration,
}

#[derive(Debug, Clone, Default)]
pub struct ConsoleConfigOverrides {
    pub bind: Option<String>,
    pub assets_dir: Option<PathBuf>,
    pub rpc_timeout_ms: Option<u64>,
    pub gateway: GatewayConfigOverrides,
}

impl ConsoleConfig {
    /// Discover the console config from explicit overrides and environment.
    ///
    /// # Errors
    /// Returns an error when the gateway socket or auth token is invalid.
    pub fn discover(overrides: ConsoleConfigOverrides) -> Result<Self, ConfigError> {
        let gateway = GatewayConfig::discover(overrides.gateway)?;
        let bind = overrides
            .bind
            .or_else(|| std::env::var(SANDBOX_CONSOLE_BIND_ENV).ok())
            .unwrap_or_else(|| DEFAULT_CONSOLE_BIND.to_owned());
        let assets_dir = overrides
            .assets_dir
            .or_else(|| std::env::var_os(SANDBOX_CONSOLE_ASSETS_ENV).map(PathBuf::from))
            .or_else(default_assets_dir);
        let rpc_timeout = overrides
            .rpc_timeout_ms
            .map_or(DEFAULT_RPC_TIMEOUT, Duration::from_millis);
        Ok(Self {
            bind,
            gateway,
            assets_dir,
            rpc_timeout,
        })
    }
}

fn default_assets_dir() -> Option<PathBuf> {
    DEFAULT_ASSET_DIRS
        .iter()
        .map(PathBuf::from)
        .find(|dir| dir.join("index.html").is_file())
}
