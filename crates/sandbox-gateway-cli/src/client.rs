use std::path::PathBuf;

use sandbox_protocol::{Request, MAX_REQUEST_BYTES};
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;

const MAX_RESPONSE_BYTES: usize = MAX_REQUEST_BYTES;

#[derive(Debug)]
pub struct ManagerClient {
    socket_path: PathBuf,
}

#[derive(Debug)]
pub enum ManagerClientError {
    Transport(std::io::Error),
    Protocol(String),
    Json(serde_json::Error),
}

impl ManagerClient {
    #[must_use]
    pub fn new(socket_path: impl Into<PathBuf>) -> Self {
        Self {
            socket_path: socket_path.into(),
        }
    }

    pub async fn send(&self, request: &Request) -> Result<Value, ManagerClientError> {
        let mut stream = UnixStream::connect(&self.socket_path)
            .await
            .map_err(ManagerClientError::Transport)?;
        let request_value = serde_json::to_value(request).map_err(ManagerClientError::Json)?;
        stream
            .write_all(&json_line(&request_value))
            .await
            .map_err(ManagerClientError::Transport)?;
        stream
            .shutdown()
            .await
            .map_err(ManagerClientError::Transport)?;
        read_response_line(stream).await
    }
}

impl ManagerClientError {
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            Self::Transport(_) => "connection_error",
            Self::Protocol(_) | Self::Json(_) => "protocol_error",
        }
    }
}

impl std::fmt::Display for ManagerClientError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Transport(error) => write!(formatter, "manager connection failed: {error}"),
            Self::Protocol(message) => formatter.write_str(message),
            Self::Json(error) => write!(formatter, "manager response json failed: {error}"),
        }
    }
}

impl std::error::Error for ManagerClientError {}

async fn read_response_line(stream: UnixStream) -> Result<Value, ManagerClientError> {
    let limit = u64::try_from(MAX_RESPONSE_BYTES)
        .unwrap_or(u64::MAX)
        .saturating_add(1);
    let mut reader = BufReader::new(stream.take(limit));
    let mut line = Vec::new();
    reader
        .read_until(b'\n', &mut line)
        .await
        .map_err(ManagerClientError::Transport)?;
    if line.is_empty() {
        return Err(ManagerClientError::Protocol(
            "manager returned an empty response".to_owned(),
        ));
    }
    if line.len() > MAX_RESPONSE_BYTES {
        return Err(ManagerClientError::Protocol(format!(
            "manager response exceeded {MAX_RESPONSE_BYTES} bytes"
        )));
    }
    if !line.ends_with(b"\n") {
        return Err(ManagerClientError::Protocol(
            "manager response was not newline terminated".to_owned(),
        ));
    }
    serde_json::from_slice::<Value>(&line).map_err(ManagerClientError::Json)
}

fn json_line(value: &Value) -> Vec<u8> {
    let mut line = serde_json::to_vec(value).unwrap_or_default();
    line.push(b'\n');
    line
}
