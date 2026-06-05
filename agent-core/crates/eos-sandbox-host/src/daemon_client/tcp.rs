use std::time::Duration;

use serde_json::Value;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

use crate::provider::{DaemonTcpEndpoint, RawExecResult};

use super::{
    DAEMON_AUTH_FIELD, EMPTY_RESPONSE_MESSAGE, TCP_DEFAULT_TIMEOUT_S, THIN_CLIENT_CONNECT_FAILED,
    THIN_CLIENT_IO_FAILED,
};

enum TcpError {
    Connect(String),
    Io(String),
}

fn io_token(err: &std::io::Error) -> String {
    format!("{:?}", err.kind())
}

pub(super) async fn call_tcp_daemon(
    endpoint: &DaemonTcpEndpoint,
    envelope_json: &str,
    timeout_s: u32,
) -> RawExecResult {
    let client_timeout = Duration::from_secs(u64::from(if timeout_s == 0 {
        TCP_DEFAULT_TIMEOUT_S
    } else {
        timeout_s
    }));
    let authed = authenticated_envelope_json(envelope_json, endpoint);
    match tokio::time::timeout(client_timeout, call_tcp_daemon_inner(endpoint, &authed)).await {
        Ok(Ok(stdout)) => {
            if stdout.trim().is_empty() {
                io_failed(THIN_CLIENT_IO_FAILED, EMPTY_RESPONSE_MESSAGE.to_owned())
            } else {
                RawExecResult {
                    exit_code: 0,
                    stdout,
                    stderr: String::new(),
                    success: true,
                }
            }
        }
        Ok(Err(TcpError::Connect(token))) => io_failed(
            THIN_CLIENT_CONNECT_FAILED,
            format!("EOS_DAEMON_CONNECT_FAILED:{token}"),
        ),
        Ok(Err(TcpError::Io(token))) => io_failed(
            THIN_CLIENT_IO_FAILED,
            format!("EOS_DAEMON_IO_FAILED:{token}"),
        ),
        Err(_elapsed) => io_failed(
            THIN_CLIENT_IO_FAILED,
            "EOS_DAEMON_IO_FAILED:Elapsed".to_owned(),
        ),
    }
}

pub(super) fn io_failed(exit_code: i32, stderr: String) -> RawExecResult {
    RawExecResult {
        exit_code,
        stdout: String::new(),
        stderr,
        success: false,
    }
}

async fn call_tcp_daemon_inner(
    endpoint: &DaemonTcpEndpoint,
    envelope_json: &str,
) -> Result<String, TcpError> {
    let mut stream = TcpStream::connect((endpoint.host.as_str(), endpoint.port))
        .await
        .map_err(|e| TcpError::Connect(io_token(&e)))?;
    let exchange = async {
        stream.write_all(envelope_json.as_bytes()).await?;
        stream.write_all(b"\n").await?;
        stream.shutdown().await?; // half-close the write side (Python write_eof)
        let mut buf = Vec::new();
        stream.read_to_end(&mut buf).await?;
        Ok::<String, std::io::Error>(String::from_utf8_lossy(&buf).into_owned())
    };
    exchange.await.map_err(|e| TcpError::Io(io_token(&e)))
}

pub(crate) fn authenticated_envelope_json(
    envelope_json: &str,
    endpoint: &DaemonTcpEndpoint,
) -> String {
    if endpoint.auth_token.is_empty() {
        return envelope_json.to_owned();
    }
    match serde_json::from_str::<Value>(envelope_json) {
        Ok(Value::Object(mut map)) => {
            map.insert(
                DAEMON_AUTH_FIELD.to_owned(),
                Value::String(endpoint.auth_token.clone()),
            );
            serde_json::to_string(&Value::Object(map)).unwrap_or_else(|_| envelope_json.to_owned())
        }
        _ => envelope_json.to_owned(),
    }
}
