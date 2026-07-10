//! Typed schema for the optional `console` section of the sandbox config.

use serde::Deserialize;

use crate::configs::validate::{require_f64_gt, require_socket_addr, ConfigFieldError};

pub const DEFAULT_CONSOLE_BIND: &str = "127.0.0.1:7880";

/// Console server policy: bind address plus the five timeouts and the
/// endpoint-cache TTL, all in seconds. Flag/env overrides on the console
/// binary outrank these values.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ConsoleConfig {
    pub bind_addr: String,
    pub rpc_timeout_s: f64,
    pub health_probe_timeout_s: f64,
    pub proxy_connect_timeout_s: f64,
    pub proxy_response_timeout_s: f64,
    pub endpoint_resolve_timeout_s: f64,
    pub endpoint_cache_ttl_s: f64,
}

impl Default for ConsoleConfig {
    fn default() -> Self {
        Self {
            bind_addr: DEFAULT_CONSOLE_BIND.to_owned(),
            rpc_timeout_s: 120.0,
            health_probe_timeout_s: 2.0,
            proxy_connect_timeout_s: 10.0,
            proxy_response_timeout_s: 30.0,
            endpoint_resolve_timeout_s: 5.0,
            endpoint_cache_ttl_s: 3.0,
        }
    }
}

impl ConsoleConfig {
    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when a field violates console policy.
    pub fn validate(&self) -> Result<(), ConfigFieldError> {
        require_socket_addr(&self.bind_addr, "console.bind_addr")?;
        require_f64_gt(self.rpc_timeout_s, 0.0, "console.rpc_timeout_s")?;
        require_f64_gt(
            self.health_probe_timeout_s,
            0.0,
            "console.health_probe_timeout_s",
        )?;
        require_f64_gt(
            self.proxy_connect_timeout_s,
            0.0,
            "console.proxy_connect_timeout_s",
        )?;
        require_f64_gt(
            self.proxy_response_timeout_s,
            0.0,
            "console.proxy_response_timeout_s",
        )?;
        require_f64_gt(
            self.endpoint_resolve_timeout_s,
            0.0,
            "console.endpoint_resolve_timeout_s",
        )?;
        require_f64_gt(
            self.endpoint_cache_ttl_s,
            0.0,
            "console.endpoint_cache_ttl_s",
        )
    }
}
