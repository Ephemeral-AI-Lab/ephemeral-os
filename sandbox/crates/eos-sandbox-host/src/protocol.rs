use std::io::{BufRead, BufReader, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

use serde_json::{json, Map, Value};

pub const DAEMON_AUTH_FIELD: &str = "_eos_daemon_auth_token";
pub const DAEMON_PROTOCOL_FIELD: &str = "_eos_daemon_protocol_version";
pub const DAEMON_PROTOCOL_VERSION: i64 = 1;
pub const MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;
pub const CONNECT_RETRY_DELAYS_S: [f64; 4] = [0.25, 0.5, 1.0, 2.0];
pub const HEARTBEAT_OP: &str = "sandbox.call.heartbeat";
pub const READY_OP: &str = "sandbox.runtime.ready";
pub const DEFAULT_LAYER_STACK_ROOT: &str = "/eos/layer-stack";

#[derive(Debug, thiserror::Error)]
pub enum ClientError {
    #[error("connect {addr}: {source}")]
    Connect {
        addr: SocketAddr,
        #[source]
        source: std::io::Error,
    },
    #[error("request i/o: {0}")]
    Io(#[from] std::io::Error),
    #[error("daemon closed connection without a response")]
    EmptyResponse,
    #[error("decode response {raw:?}: {source}")]
    Decode {
        raw: String,
        #[source]
        source: serde_json::Error,
    },
}

impl ClientError {
    pub const fn is_connect_failure(&self) -> bool {
        matches!(self, Self::Connect { .. })
    }
}

#[derive(Debug, Clone)]
pub struct ProtocolClient {
    addr: SocketAddr,
    auth_token: Option<String>,
    timeout: Duration,
}

impl ProtocolClient {
    pub fn new(addr: SocketAddr, auth_token: Option<String>, timeout: Duration) -> Self {
        Self {
            addr,
            auth_token,
            timeout,
        }
    }

    pub fn with_token(&self, auth_token: Option<String>) -> Self {
        Self {
            addr: self.addr,
            auth_token,
            timeout: self.timeout,
        }
    }
    pub const fn addr(&self) -> SocketAddr {
        self.addr
    }

    pub fn request(
        &self,
        op: &str,
        invocation_id: &str,
        args: &Value,
    ) -> Result<Value, ClientError> {
        let mut line = stamped_envelope_bytes(op, invocation_id, args, self.auth_token.as_deref());
        line.push(b'\n');
        self.request_raw(&line)
    }

    pub fn request_unstamped(
        &self,
        op: &str,
        invocation_id: &str,
        args: &Value,
    ) -> Result<Value, ClientError> {
        let mut line = raw_envelope_bytes(op, invocation_id, args, self.auth_token.as_deref());
        line.push(b'\n');
        self.request_raw(&line)
    }

    pub fn request_raw(&self, line: &[u8]) -> Result<Value, ClientError> {
        let mut stream =
            TcpStream::connect_timeout(&self.addr, self.timeout).map_err(|source| {
                ClientError::Connect {
                    addr: self.addr,
                    source,
                }
            })?;
        stream.set_read_timeout(Some(self.timeout))?;
        stream.set_write_timeout(Some(self.timeout))?;
        stream.set_nodelay(true).ok();
        stream.write_all(line)?;
        stream.flush().ok();

        let mut reader = BufReader::new(stream);
        let mut response = String::new();
        let read = reader.read_line(&mut response)?;
        if read == 0 {
            return Err(ClientError::EmptyResponse);
        }
        serde_json::from_str(response.trim_end()).map_err(|source| ClientError::Decode {
            raw: response,
            source,
        })
    }
}
pub fn stamped_envelope_bytes(
    op: &str,
    invocation_id: &str,
    args: &Value,
    token: Option<&str>,
) -> Vec<u8> {
    let mut args_obj = match args {
        Value::Object(map) => map.clone(),
        _ => Map::new(),
    };
    args_obj
        .entry(DAEMON_PROTOCOL_FIELD.to_owned())
        .or_insert_with(|| json!(DAEMON_PROTOCOL_VERSION));
    args_obj
        .entry("invocation_id".to_owned())
        .or_insert_with(|| json!(invocation_id));
    raw_envelope_bytes(op, invocation_id, &Value::Object(args_obj), token)
}
pub fn raw_envelope_bytes(
    op: &str,
    invocation_id: &str,
    args: &Value,
    token: Option<&str>,
) -> Vec<u8> {
    let mut envelope = Map::new();
    envelope.insert("op".to_owned(), json!(op));
    envelope.insert("invocation_id".to_owned(), json!(invocation_id));
    envelope.insert("args".to_owned(), args.clone());
    if let Some(token) = token {
        envelope.insert(DAEMON_AUTH_FIELD.to_owned(), json!(token));
    }
    serde_json::to_vec(&Value::Object(envelope)).unwrap_or_default()
}
pub fn is_success(response: &Value) -> bool {
    response.get("success") != Some(&Value::Bool(false))
}
pub fn error_kind(response: &Value) -> Option<&str> {
    response.get("error")?.get("kind")?.as_str()
}
