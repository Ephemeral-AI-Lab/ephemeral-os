//! Async RPC server: `AF_UNIX` plus optional loopback TCP, one framed request per
//! connection, dispatch through the daemon dispatcher, and token-driven
//! shutdown. Connection handlers keep mutex guards out of await points.

mod connection;
mod dispatch;
mod lifecycle;
mod trace_context;

use std::path::PathBuf;
use std::sync::Arc;

use config::configs::daemon::DaemonConfig;
use tokio_util::sync::CancellationToken;

use crate::invocation_registry::InFlightRegistry;

const MAX_REQUEST_BYTES: usize = crate::wire::MAX_REQUEST_BYTES;
#[cfg(not(test))]
const REQUEST_READ_TIMEOUT_S: f64 = crate::wire::REQUEST_READ_TIMEOUT_S;
#[cfg(test)]
const REQUEST_READ_TIMEOUT_S: f64 = 0.1;

/// Where the daemon binds + writes its pid, plus the optional TCP listener.
#[derive(Debug, Clone)]
pub struct ServerConfig {
    /// `AF_UNIX` socket path (chmod 0o600 after bind).
    pub socket_path: PathBuf,
    /// Pid file path written after the listeners bind.
    pub pid_path: PathBuf,
    /// Optional loopback TCP host (e.g. `127.0.0.1`).
    pub tcp_host: Option<String>,
    /// Optional loopback TCP port; both host+port enable the TCP listener.
    pub tcp_port: Option<u16>,
    /// TCP-only auth token; popped from each TCP request before dispatch.
    pub auth_token: Option<String>,
    /// Host-forward TCP auth token; permits host-only and operator daemon ops.
    pub forward_auth_token: Option<String>,
}

/// The running daemon: request dispatch state, invocation registry, and
/// shutdown token.
pub struct DaemonServer {
    config: ServerConfig,
    invocation_registry: Arc<InFlightRegistry>,
    shutdown: CancellationToken,
}

impl DaemonServer {
    /// Assemble a daemon over `config`, wiring the invocation registry and
    /// shutdown token.
    #[must_use]
    pub fn new(config: ServerConfig) -> Self {
        Self {
            config,
            invocation_registry: Arc::new(InFlightRegistry::new(
                crate::DEFAULT_TTL_S,
                crate::DEFAULT_REAPER_INTERVAL_S,
            )),
            shutdown: CancellationToken::new(),
        }
    }

    /// Assemble a daemon using the typed `daemon` config section loaded from
    /// `eos-sandbox/config/prd.yml`.
    #[must_use]
    pub fn with_daemon_config(
        config: ServerConfig,
        daemon_config: &DaemonConfig,
        _isolated_config: &config::configs::isolated::IsolatedNetworkConfig,
    ) -> Self {
        Self {
            config,
            invocation_registry: Arc::new(InFlightRegistry::new(
                daemon_config.inflight.ttl_s,
                daemon_config.inflight.reaper_interval_s,
            )),
            shutdown: CancellationToken::new(),
        }
    }

    /// The shutdown token; cancel it to drain + tear down the serve loops.
    pub fn shutdown_token(&self) -> CancellationToken {
        self.shutdown.clone()
    }
}
