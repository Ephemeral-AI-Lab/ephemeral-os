//! The daemon HTTP listener: its own loopback TCP transport, separate from the
//! JSON-line RPC listener with no sniffing or multiplexing. Accepts connections
//! until shutdown and serves each over HTTP/1.1 with upgrade support.

use std::convert::Infallible;
use std::sync::Arc;

use http::{Request, Response};
use hyper::body::Incoming;
use hyper::service::service_fn;
use hyper_util::rt::TokioIo;
use sandbox_observability_telemetry::Observer;
use sandbox_runtime::SandboxRuntimeOperations;
use tokio::net::{TcpListener, TcpStream};
use tokio::task::JoinHandle;
use tokio::time::{timeout, Duration};
use tokio_util::sync::CancellationToken;
use tokio_util::task::TaskTracker;

use crate::rpc::{BlockingAdmission, ConnectionAdmission, ServerConfig};

use super::response::BoxBody;
use super::router;

/// Shared state every request handler reads: runtime session state for target
/// resolution and the observer for the forward span.
pub(crate) struct HttpState {
    pub(crate) config: ServerConfig,
    pub(crate) operations: Arc<SandboxRuntimeOperations>,
    pub(crate) observer: Observer,
    pub(crate) blocking_admission: BlockingAdmission,
}

impl HttpState {
    pub(crate) fn sandbox_id(&self) -> &str {
        self.config.sandbox_id.as_deref().unwrap_or("daemon-http")
    }
}

/// Spawn the accept loop on the daemon runtime; it returns when `shutdown`
/// fires.
pub(crate) fn spawn(
    listener: TcpListener,
    config: ServerConfig,
    operations: Arc<SandboxRuntimeOperations>,
    observer: Observer,
    blocking_admission: BlockingAdmission,
    connection_admission: ConnectionAdmission,
    connection_tasks: TaskTracker,
    shutdown: CancellationToken,
) -> JoinHandle<()> {
    let state = Arc::new(HttpState {
        config,
        operations,
        observer,
        blocking_admission,
    });
    tokio::spawn(accept_loop(
        listener,
        state,
        connection_admission,
        connection_tasks,
        shutdown,
    ))
}

async fn accept_loop(
    listener: TcpListener,
    state: Arc<HttpState>,
    connection_admission: ConnectionAdmission,
    connection_tasks: TaskTracker,
    shutdown: CancellationToken,
) {
    loop {
        tokio::select! {
            () = shutdown.cancelled() => break,
            accepted = listener.accept() => {
                let Ok((stream, _peer)) = accepted else { continue };
                let Some(permit) = connection_admission.try_acquire() else {
                    reject_overloaded_connection(
                        stream,
                        connection_admission.limit(),
                        state.config.limits.max_request_bytes,
                    ).await;
                    continue;
                };
                let state = Arc::clone(&state);
                connection_tasks.spawn(async move {
                    let _permit = permit;
                    serve_connection(stream, state).await;
                });
            }
        }
    }
}

async fn reject_overloaded_connection(
    mut stream: TcpStream,
    max_concurrent_connections: usize,
    max_request_bytes: usize,
) {
    use tokio::io::AsyncWriteExt as _;

    let body = serde_json::to_vec(&sandbox_operation_contract::error_response_with_details(
        "server_busy",
        "daemon is at connection capacity",
        serde_json::json!({
            "fields": { "max_concurrent_connections": max_concurrent_connections }
        }),
    ))
    .unwrap_or_default();
    let head = format!(
        "HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(head.as_bytes()).await;
    let _ = stream.write_all(&body).await;
    let _ = stream.flush().await;

    // A TCP close with unread request bytes is emitted as RST on some kernels,
    // which can discard the structured 503 already written above. Drain at
    // most one bounded request before closing; the deadline keeps overload
    // rejection out of the normal work queue and prevents slow clients from
    // stalling the accept loop.
    let _ = timeout(
        Duration::from_millis(100),
        drain_one_request(&mut stream, max_request_bytes),
    )
    .await;
    let _ = stream.shutdown().await;
}

async fn drain_one_request(stream: &mut TcpStream, max_request_bytes: usize) {
    use tokio::io::AsyncReadExt as _;

    const MAX_HEAD_BYTES: usize = 64 * 1024;
    let max_total = MAX_HEAD_BYTES.saturating_add(max_request_bytes);
    let mut captured = Vec::with_capacity(4 * 1024);
    let mut received = 0_usize;
    let mut expected_total = None;
    let mut buffer = [0_u8; 4 * 1024];

    loop {
        let Ok(read) = stream.read(&mut buffer).await else {
            return;
        };
        if read == 0 {
            return;
        }
        received = received.saturating_add(read);

        if expected_total.is_none() && captured.len() < MAX_HEAD_BYTES {
            let remaining = MAX_HEAD_BYTES - captured.len();
            captured.extend_from_slice(&buffer[..read.min(remaining)]);
            if let Some(split) = captured.windows(4).position(|part| part == b"\r\n\r\n") {
                let head_end = split + 4;
                let content_length = std::str::from_utf8(&captured[..head_end])
                    .ok()
                    .and_then(|head| {
                        head.lines().find_map(|line| {
                            let (name, value) = line.split_once(':')?;
                            name.eq_ignore_ascii_case("content-length")
                                .then(|| value.trim().parse::<usize>().ok())
                                .flatten()
                        })
                    })
                    .unwrap_or(0)
                    .min(max_request_bytes);
                expected_total = Some(head_end.saturating_add(content_length));
            }
        }

        if expected_total.is_some_and(|expected| received >= expected) || received >= max_total {
            return;
        }
    }
}

async fn serve_connection(stream: TcpStream, state: Arc<HttpState>) {
    let service = service_fn(move |req: Request<Incoming>| {
        let state = Arc::clone(&state);
        async move { Ok::<Response<BoxBody>, Infallible>(router::route(state, req).await) }
    });
    let _ = hyper::server::conn::http1::Builder::new()
        .serve_connection(TokioIo::new(stream), service)
        .with_upgrades()
        .await;
}
