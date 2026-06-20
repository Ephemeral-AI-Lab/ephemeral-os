use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

use serde_json::{json, Map, Value};

pub const DAEMON_AUTH_FIELD: &str = "_eos_daemon_auth_token";
pub const DAEMON_FORWARD_AUTH_FIELD: &str = "_eos_daemon_forward_auth_token";
pub const DAEMON_PROTOCOL_FIELD: &str = "_eos_daemon_protocol_version";
pub const DAEMON_PROTOCOL_VERSION: i64 = 1;
pub const MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_RESPONSE_BYTES: usize = 16 * 1024 * 1024;
pub const CONNECT_RETRY_DELAYS_S: [f64; 4] = [0.25, 0.5, 1.0, 2.0];
pub(crate) const HEARTBEAT_OP: &str = "sandbox.call.heartbeat";
pub(crate) const READY_OP: &str = "sandbox.runtime.ready";
pub(crate) const DEFAULT_LAYER_STACK_ROOT: &str = "/eos/layer-stack";

#[derive(Debug, thiserror::Error)]
pub enum ClientError {
    #[error("connect {addr}: {source}")]
    Connect {
        addr: SocketAddr,
        #[source]
        source: std::io::Error,
    },
    #[error("request i/o setup: {0}")]
    Io(std::io::Error),
    #[error("write request: {0}")]
    Write(#[source] std::io::Error),
    #[error("read response: {0}")]
    Read(#[source] std::io::Error),
    #[error("daemon closed connection without a response")]
    EmptyResponse,
    #[error("daemon response exceeds {limit} byte limit")]
    ResponseTooLarge { limit: usize },
    #[error("decode response: {source} (raw_len={raw_len}, raw_sha256={raw_sha256})")]
    Decode {
        raw_len: usize,
        raw_sha256: String,
        #[source]
        source: serde_json::Error,
    },
}

impl ClientError {
    pub(crate) const fn is_connect_failure(&self) -> bool {
        matches!(self, Self::Connect { .. })
    }
}

#[derive(Debug, Clone)]
pub struct ProtocolClient {
    addr: SocketAddr,
    auth_token: Option<String>,
    forward_auth_token: Option<String>,
    timeout: Duration,
}

impl ProtocolClient {
    pub fn new(addr: SocketAddr, auth_token: Option<String>, timeout: Duration) -> Self {
        Self {
            addr,
            auth_token,
            forward_auth_token: None,
            timeout,
        }
    }

    pub fn new_forward_authorized(
        addr: SocketAddr,
        forward_auth_token: Option<String>,
        timeout: Duration,
    ) -> Self {
        Self {
            addr,
            auth_token: None,
            forward_auth_token,
            timeout,
        }
    }

    #[cfg(feature = "e2e-support")]
    pub fn with_token(&self, auth_token: Option<String>) -> Self {
        Self {
            addr: self.addr,
            auth_token,
            forward_auth_token: None,
            timeout: self.timeout,
        }
    }

    #[cfg(feature = "e2e-support")]
    pub fn with_forward_token(&self, forward_auth_token: Option<String>) -> Self {
        Self {
            addr: self.addr,
            auth_token: None,
            forward_auth_token,
            timeout: self.timeout,
        }
    }
    pub(crate) const fn addr(&self) -> SocketAddr {
        self.addr
    }

    pub fn request(
        &self,
        op: &str,
        invocation_id: &str,
        args: &Value,
    ) -> Result<Value, ClientError> {
        let mut line = encode_request_with_auth(op, invocation_id, args, self.transport_auth());
        line.push(b'\n');
        self.request_raw(&line)
    }

    pub fn request_raw(&self, line: &[u8]) -> Result<Value, ClientError> {
        self.request_raw_observed(line)
            .map(|response| response.value)
    }

    pub(crate) fn request_raw_observed(
        &self,
        line: &[u8],
    ) -> Result<ProtocolResponse, ClientError> {
        let mut stream =
            TcpStream::connect_timeout(&self.addr, self.timeout).map_err(|source| {
                ClientError::Connect {
                    addr: self.addr,
                    source,
                }
            })?;
        stream
            .set_read_timeout(Some(self.timeout))
            .map_err(ClientError::Io)?;
        stream
            .set_write_timeout(Some(self.timeout))
            .map_err(ClientError::Io)?;
        stream.set_nodelay(true).ok();
        stream.write_all(line).map_err(ClientError::Write)?;
        stream.flush().ok();

        let mut reader = BufReader::new(stream);
        let response = read_response_line(&mut reader)?;
        let value =
            serde_json::from_str(response.trim_end()).map_err(|source| ClientError::Decode {
                raw_len: response.len(),
                raw_sha256: trace::sha256_hex(response.as_bytes()),
                source,
            })?;
        Ok(ProtocolResponse {
            value,
            raw_bytes: response.into_bytes(),
        })
    }
}

fn read_response_line(reader: &mut impl BufRead) -> Result<String, ClientError> {
    read_response_line_with_limit(reader, MAX_RESPONSE_BYTES)
}

pub(crate) fn read_response_line_with_limit(
    reader: &mut impl BufRead,
    max_response_bytes: usize,
) -> Result<String, ClientError> {
    let limit = u64::try_from(max_response_bytes)
        .unwrap_or(u64::MAX)
        .saturating_add(1);
    let mut limited = reader.take(limit);
    let mut response = String::new();
    let read = limited
        .read_line(&mut response)
        .map_err(ClientError::Read)?;
    if read == 0 {
        return Err(ClientError::EmptyResponse);
    }
    if response.len() > max_response_bytes {
        return Err(ClientError::ResponseTooLarge {
            limit: max_response_bytes,
        });
    }
    Ok(response)
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ProtocolResponse {
    pub value: Value,
    pub raw_bytes: Vec<u8>,
}

#[allow(dead_code)]
pub fn encode_request_with_metadata(
    op: &str,
    invocation_id: &str,
    args: &Value,
    token: Option<&str>,
) -> Vec<u8> {
    encode_request_with_auth(op, invocation_id, args, TransportAuth::Raw(token))
}

pub(crate) fn encode_request_with_forward_metadata(
    op: &str,
    invocation_id: &str,
    args: &Value,
    token: Option<&str>,
) -> Vec<u8> {
    encode_request_with_auth(op, invocation_id, args, TransportAuth::Forward(token))
}

fn encode_request_with_auth(
    op: &str,
    invocation_id: &str,
    args: &Value,
    auth: TransportAuth<'_>,
) -> Vec<u8> {
    encode_request(
        op,
        invocation_id,
        &Value::Object(stamped_args(args, invocation_id)),
        auth,
    )
}

fn stamped_args(args: &Value, invocation_id: &str) -> Map<String, Value> {
    let mut args_obj = match args {
        Value::Object(map) => map.clone(),
        _ => Map::new(),
    };
    args_obj.insert(
        DAEMON_PROTOCOL_FIELD.to_owned(),
        json!(DAEMON_PROTOCOL_VERSION),
    );
    args_obj
        .entry("invocation_id".to_owned())
        .or_insert_with(|| json!(invocation_id));
    args_obj
}
fn encode_request(op: &str, invocation_id: &str, args: &Value, auth: TransportAuth<'_>) -> Vec<u8> {
    serde_json::to_vec(&Value::Object(request_object(
        op,
        invocation_id,
        args,
        auth,
    )))
    .unwrap_or_default()
}

fn request_object(
    op: &str,
    invocation_id: &str,
    args: &Value,
    auth: TransportAuth<'_>,
) -> Map<String, Value> {
    let mut request = Map::new();
    request.insert("op".to_owned(), json!(op));
    request.insert("invocation_id".to_owned(), json!(invocation_id));
    request.insert("args".to_owned(), args.clone());
    match auth {
        TransportAuth::Raw(Some(token)) => {
            request.insert(DAEMON_AUTH_FIELD.to_owned(), json!(token));
        }
        TransportAuth::Forward(Some(token)) => {
            request.insert(DAEMON_FORWARD_AUTH_FIELD.to_owned(), json!(token));
        }
        TransportAuth::Raw(None) | TransportAuth::Forward(None) => {}
    }
    request
}

#[derive(Debug, Clone, Copy)]
pub(crate) enum TransportAuth<'a> {
    Raw(Option<&'a str>),
    Forward(Option<&'a str>),
}

impl ProtocolClient {
    fn transport_auth(&self) -> TransportAuth<'_> {
        if let Some(token) = self.forward_auth_token.as_deref() {
            TransportAuth::Forward(Some(token))
        } else {
            TransportAuth::Raw(self.auth_token.as_deref())
        }
    }
}

pub fn response_envelope_status(response: &Value) -> &str {
    response
        .get("status")
        .and_then(Value::as_str)
        .filter(|status| valid_response_status(status))
        .unwrap_or("error")
}

#[allow(dead_code)]
pub fn response_domain_status(response: &Value) -> Option<&str> {
    response
        .get("result")
        .and_then(|result| result.get("status"))
        .and_then(Value::as_str)
}

pub fn response_status(response: &Value) -> &str {
    response_envelope_status(response)
}

fn valid_response_status(status: &str) -> bool {
    matches!(
        status,
        "ok" | "running" | "rejected" | "cancelled" | "timed_out" | "error"
    )
}

pub fn response_fault_kind(response: &Value) -> Option<&str> {
    response
        .get("error")
        .and_then(|error| error.get("kind"))
        .and_then(Value::as_str)
        .or_else(|| {
            (response.get("status").and_then(Value::as_str).is_none()).then_some("missing_status")
        })
}

pub fn response_is_accepted(response: &Value) -> bool {
    matches!(response_envelope_status(response), "ok" | "running")
}
