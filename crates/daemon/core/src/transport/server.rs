//! Async RPC server: `AF_UNIX` plus optional loopback TCP, one framed request per
//! connection, dispatch through daemon operations, and token-driven
//! shutdown. Connection handlers keep mutex guards out of await points.

pub(crate) mod connection;
pub(crate) mod dispatch;
mod lifecycle;

use std::path::PathBuf;
use std::sync::Arc;

use daemon_operation::DaemonOperations;
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;

pub(crate) const MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;
pub(crate) const REQUEST_READ_TIMEOUT_S: f64 = 30.0;

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

/// The running daemon: request dispatch state and shutdown token.
pub struct DaemonServer {
    config: ServerConfig,
    operations: Arc<DaemonOperations>,
    shutdown: CancellationToken,
}

impl DaemonServer {
    /// Assemble a daemon over `config`, wiring the shutdown token.
    #[must_use]
    pub fn new(config: ServerConfig, operations: Arc<DaemonOperations>) -> Self {
        Self {
            config,
            operations,
            shutdown: CancellationToken::new(),
        }
    }
}

pub(super) fn error_response(
    kind: &'static str,
    message: impl Into<String>,
    details: Value,
) -> Value {
    json!({
        "status": "error",
        "error": {
            "kind": kind,
            "message": message.into(),
            "details": fault_details(details),
        },
        "meta": {
            "envelope_version": 2,
            "op": "",
            "request_id": "",
            "duration_ms": 0.0,
            "resource_summary": { "fields": {} },
            "warnings": [],
        },
    })
}

fn fault_details(details: Value) -> Value {
    match details {
        Value::Null => json!({}),
        Value::Object(fields) if fields.is_empty() => json!({}),
        Value::Object(fields) => json!({ "fields": fields }),
        value => json!({ "fields": { "value": value } }),
    }
}
