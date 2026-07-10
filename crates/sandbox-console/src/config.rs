//! Console configuration: loopback bind address, gateway endpoint and auth
//! token (via the shared CLI discovery), the SPA asset directory, and the
//! server timeouts from the optional `console` YAML section.

use std::path::PathBuf;
use std::time::Duration;

use sandbox_cli::core::{GatewayConfig, GatewayConfigOverrides};
use sandbox_config::configs::console::ConsoleConfig as ConsoleSection;

pub const SANDBOX_CONSOLE_BIND_ENV: &str = "SANDBOX_CONSOLE_BIND";
pub const SANDBOX_CONSOLE_ASSETS_ENV: &str = "SANDBOX_CONSOLE_ASSETS";
pub const SANDBOX_CONSOLE_CONFIG_YAML_ENV: &str = "SANDBOX_CONSOLE_CONFIG_YAML";

const DEFAULT_ASSET_DIRS: &[&str] = &["dist/console", "web/console/dist"];

#[derive(Debug, Clone)]
pub struct ConsoleConfig {
    pub bind: String,
    pub gateway: GatewayConfig,
    pub assets_dir: Option<PathBuf>,
    pub rpc_timeout: Duration,
    pub health_probe_timeout: Duration,
    pub proxy_connect_timeout: Duration,
    pub proxy_response_timeout: Duration,
    pub endpoint_resolve_timeout: Duration,
    pub endpoint_cache_ttl: Duration,
}

#[derive(Debug, Clone, Default)]
pub struct ConsoleConfigOverrides {
    pub bind: Option<String>,
    pub assets_dir: Option<PathBuf>,
    pub rpc_timeout_ms: Option<u64>,
    pub config_yaml: Option<PathBuf>,
    pub gateway: GatewayConfigOverrides,
}

impl ConsoleConfig {
    /// Discover the console config from explicit overrides, environment, and
    /// the optional `console` section of the YAML named by `--config-yaml` /
    /// `SANDBOX_CONSOLE_CONFIG_YAML`. Flag/env overrides outrank YAML.
    ///
    /// # Errors
    /// Returns an error when the gateway socket or auth token is invalid, or
    /// when the YAML document fails to load or validate.
    pub fn discover(overrides: ConsoleConfigOverrides) -> Result<Self, Box<dyn std::error::Error>> {
        let yaml_path = overrides
            .config_yaml
            .clone()
            .or_else(|| std::env::var_os(SANDBOX_CONSOLE_CONFIG_YAML_ENV).map(PathBuf::from));
        let section = load_console_section(yaml_path)?;
        let gateway = GatewayConfig::discover(overrides.gateway.clone())?;
        Ok(Self::from_sources(
            overrides,
            std::env::var(SANDBOX_CONSOLE_BIND_ENV).ok(),
            std::env::var_os(SANDBOX_CONSOLE_ASSETS_ENV).map(PathBuf::from),
            section,
            gateway,
        ))
    }

    /// Resolve the effective config from already-gathered sources with
    /// flag > env > YAML section > default precedence. `section` must be
    /// validated; the gateway endpoint resolves separately via
    /// [`GatewayConfig::discover`].
    #[must_use]
    pub fn from_sources(
        overrides: ConsoleConfigOverrides,
        env_bind: Option<String>,
        env_assets: Option<PathBuf>,
        section: ConsoleSection,
        gateway: GatewayConfig,
    ) -> Self {
        let bind = overrides
            .bind
            .or(env_bind)
            .unwrap_or_else(|| section.bind_addr.clone());
        let assets_dir = overrides
            .assets_dir
            .or(env_assets)
            .or_else(default_assets_dir);
        let rpc_timeout = overrides.rpc_timeout_ms.map_or_else(
            || Duration::from_secs_f64(section.rpc_timeout_s),
            Duration::from_millis,
        );
        Self {
            bind,
            gateway,
            assets_dir,
            rpc_timeout,
            health_probe_timeout: Duration::from_secs_f64(section.health_probe_timeout_s),
            proxy_connect_timeout: Duration::from_secs_f64(section.proxy_connect_timeout_s),
            proxy_response_timeout: Duration::from_secs_f64(section.proxy_response_timeout_s),
            endpoint_resolve_timeout: Duration::from_secs_f64(section.endpoint_resolve_timeout_s),
            endpoint_cache_ttl: Duration::from_secs_f64(section.endpoint_cache_ttl_s),
        }
    }
}

fn load_console_section(
    path: Option<PathBuf>,
) -> Result<ConsoleSection, Box<dyn std::error::Error>> {
    let Some(path) = path else {
        return Ok(ConsoleSection::default());
    };
    let document = sandbox_config::load_path(&path)?;
    let section = match document.section::<ConsoleSection>("console") {
        Ok(section) => section,
        Err(sandbox_config::ConfigError::MissingSection { .. }) => ConsoleSection::default(),
        Err(error) => return Err(error.into()),
    };
    section.validate()?;
    Ok(section)
}

fn default_assets_dir() -> Option<PathBuf> {
    DEFAULT_ASSET_DIRS
        .iter()
        .map(PathBuf::from)
        .find(|dir| dir.join("index.html").is_file())
}
