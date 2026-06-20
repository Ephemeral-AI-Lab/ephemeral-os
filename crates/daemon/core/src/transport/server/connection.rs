use std::net::SocketAddr;
use std::time::Duration;

use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader};
use tokio::time::timeout;

use super::{DaemonServer, MAX_REQUEST_BYTES, REQUEST_READ_TIMEOUT_S};
use crate::error::DaemonError;
use crate::wire::{encode, WireMessage};

impl DaemonServer {
    /// Handle one accepted connection: read one capped, timed request line, pop
    /// the TCP-only auth token, decode the request, dispatch, write one framed
    /// response. Per-connection; never holds a lock across the await points.
    pub(super) async fn handle_connection<S>(
        &self,
        stream: S,
        is_tcp: bool,
        _peer_addr: Option<SocketAddr>,
        _local_addr: Option<SocketAddr>,
    ) -> Result<(), DaemonError>
    where
        S: AsyncRead + AsyncWrite + Unpin,
    {
        let (mut reader, mut writer) = tokio::io::split(stream);
        let bytes = read_request_line(&mut reader).await;
        let response = match bytes {
            Ok(bytes) => self.dispatch_bytes(bytes, is_tcp).await,
            Err(err @ DaemonError::RequestTooLarge { .. }) => crate::dispatcher::error_response(
                err.wire_kind(),
                format!("daemon request exceeds {MAX_REQUEST_BYTES} byte limit"),
                serde_json::json!({"limit": MAX_REQUEST_BYTES}),
            ),
            Err(err) => crate::dispatcher::error_response(
                err.wire_kind(),
                err.to_string(),
                serde_json::json!({}),
            ),
        };
        let framed = encode(&WireMessage::Response(response.clone()))?;
        if let Err(err) = writer.write_all(&framed).await {
            return Err(DaemonError::Io(err));
        }
        if let Err(err) = writer.shutdown().await {
            return Err(DaemonError::Io(err));
        }
        Ok(())
    }
}

async fn read_request_line<R>(reader: &mut R) -> Result<Vec<u8>, DaemonError>
where
    R: AsyncRead + Unpin,
{
    read_request_line_with_timeout(reader, REQUEST_READ_TIMEOUT_S).await
}

pub(crate) async fn read_request_line_with_timeout<R>(
    reader: &mut R,
    timeout_s: f64,
) -> Result<Vec<u8>, DaemonError>
where
    R: AsyncRead + Unpin,
{
    let mut buf = Vec::new();
    let read = async {
        let limit = u64::try_from(MAX_REQUEST_BYTES)
            .unwrap_or(u64::MAX)
            .saturating_add(1);
        let mut limited = BufReader::new(reader.take(limit));
        limited.read_until(b'\n', &mut buf).await?;
        if buf.len() > MAX_REQUEST_BYTES {
            return Err(DaemonError::RequestTooLarge {
                limit: MAX_REQUEST_BYTES,
            });
        }
        Ok::<(), DaemonError>(())
    };
    timeout(Duration::from_secs_f64(timeout_s), read)
        .await
        .map_err(|_| {
            DaemonError::Io(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "daemon request read timed out",
            ))
        })??;
    Ok(buf)
}
