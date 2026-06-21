use std::time::Duration;

use sandbox_protocol::{decode_request_value, Request};
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader};
use tokio::time::timeout;

use super::{GatewayError, SandboxGatewayServer};

impl SandboxGatewayServer {
    pub async fn handle_connection<S>(&self, stream: S) -> Result<(), GatewayError>
    where
        S: AsyncRead + AsyncWrite + Unpin,
    {
        let (mut reader, mut writer) = tokio::io::split(stream);
        let response = match read_request_line(&mut reader).await {
            Ok(bytes) => match decode_request_bytes(&bytes) {
                Ok(request) => self
                    .manager
                    .dispatch_request(request)
                    .await
                    .into_json_value(),
                Err(error) => error.to_response_value(),
            },
            Err(error) => error.to_response_value(),
        };
        writer
            .write_all(&sandbox_protocol::response_line(&response))
            .await?;
        writer.shutdown().await?;
        Ok(())
    }
}

async fn read_request_line<R>(reader: &mut R) -> Result<Vec<u8>, GatewayError>
where
    R: AsyncRead + Unpin,
{
    let mut buf = Vec::new();
    let read = async {
        let limit = u64::try_from(sandbox_protocol::MAX_REQUEST_BYTES)
            .unwrap_or(u64::MAX)
            .saturating_add(1);
        let mut limited = BufReader::new(reader.take(limit));
        limited.read_until(b'\n', &mut buf).await?;
        if buf.len() > sandbox_protocol::MAX_REQUEST_BYTES {
            return Err(GatewayError::RequestTooLarge {
                limit: sandbox_protocol::MAX_REQUEST_BYTES,
            });
        }
        if !buf.ends_with(b"\n") {
            return Err(GatewayError::MissingNewline);
        }
        Ok::<(), GatewayError>(())
    };
    timeout(
        Duration::from_secs_f64(sandbox_protocol::REQUEST_READ_TIMEOUT_S),
        read,
    )
    .await
    .map_err(|_| {
        GatewayError::Io(std::io::Error::new(
            std::io::ErrorKind::TimedOut,
            "gateway request read timed out",
        ))
    })??;
    Ok(buf)
}

fn decode_request_bytes(bytes: &[u8]) -> Result<Request, GatewayError> {
    let value = serde_json::from_slice::<Value>(bytes)?;
    decode_request_value(value).map_err(|error| GatewayError::BadRequest {
        kind: error.kind(),
        message: error.message().to_owned(),
    })
}
