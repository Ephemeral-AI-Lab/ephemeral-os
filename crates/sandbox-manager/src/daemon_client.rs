use std::thread;
use std::time::Duration;

use crate::{ManagerError, SandboxDaemonEndpoint};
use sandbox_protocol::DAEMON_AUTH_FIELD;
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;

const MAX_RESPONSE_BYTES: usize = sandbox_protocol::ProtocolLimits::DEFAULT_MAX_REQUEST_BYTES;

pub trait SandboxDaemonClient: Send + Sync {
    fn invoke_with_timeout(
        &self,
        endpoint: &SandboxDaemonEndpoint,
        request: sandbox_protocol::Request,
        timeout: Duration,
    ) -> Result<sandbox_protocol::Response, ManagerError>;
}

#[derive(Debug, Default, Clone, Copy)]
pub struct TcpSandboxDaemonClient;

impl TcpSandboxDaemonClient {
    #[must_use]
    pub const fn new() -> Self {
        Self
    }
}

impl SandboxDaemonClient for TcpSandboxDaemonClient {
    fn invoke_with_timeout(
        &self,
        endpoint: &SandboxDaemonEndpoint,
        request: sandbox_protocol::Request,
        timeout: Duration,
    ) -> Result<sandbox_protocol::Response, ManagerError> {
        let host = endpoint.host.clone();
        let port = endpoint.port;
        let request_line = request_line(&request, &endpoint.auth_token)?;
        if tokio::runtime::Handle::try_current().is_ok() {
            let worker = thread::Builder::new()
                .name("sandbox-daemon-client".to_owned())
                .spawn(move || run_exchange(host, port, request_line, timeout))
                .map_err(|error| ManagerError::ForwardingFailed {
                    message: format!("failed to spawn daemon client worker: {error}"),
                })?;
            return worker.join().map_err(|_| ManagerError::ForwardingFailed {
                message: "daemon client worker panicked".to_owned(),
            })?;
        }
        run_exchange(host, port, request_line, timeout)
    }
}

fn run_exchange(
    host: String,
    port: u16,
    request_line: Vec<u8>,
    timeout: Duration,
) -> Result<sandbox_protocol::Response, ManagerError> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
        .map_err(|error| ManagerError::ForwardingFailed {
            message: format!("failed to build daemon client runtime: {error}"),
        })?;
    runtime.block_on(async move {
        tokio::time::timeout(timeout, tcp_exchange(host, port, request_line))
            .await
            .map_err(|_| ManagerError::ForwardingFailed {
                message: format!("daemon request timed out after {} ms", timeout.as_millis()),
            })?
    })
}

async fn tcp_exchange(
    host: String,
    port: u16,
    request_line: Vec<u8>,
) -> Result<sandbox_protocol::Response, ManagerError> {
    let mut stream = TcpStream::connect((host.as_str(), port))
        .await
        .map_err(|error| ManagerError::ForwardingFailed {
            message: format!("connect {host}:{port} failed: {error}"),
        })?;
    stream
        .write_all(&request_line)
        .await
        .map_err(|error| ManagerError::ForwardingFailed {
            message: format!("write daemon request failed: {error}"),
        })?;
    stream
        .shutdown()
        .await
        .map_err(|error| ManagerError::ForwardingFailed {
            message: format!("shutdown daemon request stream failed: {error}"),
        })?;
    read_response_line(stream)
        .await
        .map(sandbox_protocol::Response::ok)
}

async fn read_response_line<S>(stream: S) -> Result<Value, ManagerError>
where
    S: AsyncRead + Unpin,
{
    let limit = u64::try_from(MAX_RESPONSE_BYTES)
        .unwrap_or(u64::MAX)
        .saturating_add(1);
    let mut reader = BufReader::new(stream.take(limit));
    let mut line = Vec::new();
    reader
        .read_until(b'\n', &mut line)
        .await
        .map_err(|error| ManagerError::ForwardingFailed {
            message: format!("read daemon response failed: {error}"),
        })?;
    if line.is_empty() {
        return Err(ManagerError::ForwardingFailed {
            message: "daemon returned an empty response".to_owned(),
        });
    }
    if line.len() > MAX_RESPONSE_BYTES {
        return Err(ManagerError::ForwardingFailed {
            message: format!("daemon response exceeded {MAX_RESPONSE_BYTES} bytes"),
        });
    }
    if !line.ends_with(b"\n") {
        return Err(ManagerError::ForwardingFailed {
            message: "daemon response was not newline terminated".to_owned(),
        });
    }
    serde_json::from_slice::<Value>(&line).map_err(|error| ManagerError::ForwardingFailed {
        message: format!("decode daemon response failed: {error}"),
    })
}

fn request_line(
    request: &sandbox_protocol::Request,
    auth_token: &str,
) -> Result<Vec<u8>, ManagerError> {
    let mut value =
        serde_json::to_value(request).map_err(|error| ManagerError::ForwardingFailed {
            message: format!("encode daemon request failed: {error}"),
        })?;
    if let Value::Object(map) = &mut value {
        map.insert(
            DAEMON_AUTH_FIELD.to_owned(),
            Value::String(auth_token.to_owned()),
        );
    }
    let mut line = serde_json::to_vec(&value).map_err(|error| ManagerError::ForwardingFailed {
        message: format!("encode daemon request failed: {error}"),
    })?;
    if line.len().saturating_add(1) > MAX_RESPONSE_BYTES {
        return Err(ManagerError::ForwardingFailed {
            message: format!("daemon request exceeds {MAX_RESPONSE_BYTES} byte limit"),
        });
    }
    line.push(b'\n');
    Ok(line)
}
