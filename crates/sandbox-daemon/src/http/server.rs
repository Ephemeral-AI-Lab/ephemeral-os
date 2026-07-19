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

use crate::rpc::{AdmissionError, BlockingAdmission, ConnectionAdmission, ServerConfig};

use super::response::BoxBody;
use super::router;

/// Shared state every request handler reads: runtime session state for target
/// resolution and the observer for the forward span.
pub(crate) struct HttpState {
    pub(crate) config: ServerConfig,
    pub(crate) operations: Arc<SandboxRuntimeOperations>,
    pub(crate) observer: Observer,
    pub(crate) blocking_admission: BlockingAdmission,
    pub(crate) async_tasks: TaskTracker,
    pub(crate) shutdown: CancellationToken,
}

impl HttpState {
    pub(crate) fn sandbox_id(&self) -> &str {
        self.config.sandbox_id.as_deref().unwrap_or("daemon-http")
    }
}

/// Spawn the accept loop on the daemon runtime; it returns when `shutdown`
/// fires.
#[expect(
    clippy::too_many_arguments,
    reason = "the accept-loop dependencies have distinct ownership and lifetimes"
)]
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
        async_tasks: connection_tasks.clone(),
        shutdown: shutdown.clone(),
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
                let permit = match connection_admission.try_acquire() {
                    Ok(permit) => permit,
                    Err(reason) => {
                        reject_connection(
                            stream,
                            reason,
                            connection_admission.limit(),
                            state.config.limits.max_request_bytes,
                        ).await;
                        continue;
                    }
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

async fn reject_connection(
    mut stream: TcpStream,
    reason: AdmissionError,
    max_concurrent_connections: usize,
    max_request_bytes: usize,
) {
    use tokio::io::AsyncWriteExt as _;

    let (kind, message, details) = match reason {
        AdmissionError::Capacity => (
            "server_busy",
            "daemon is at connection capacity",
            serde_json::json!({
                "fields": { "max_concurrent_connections": max_concurrent_connections }
            }),
        ),
        AdmissionError::Closed => (
            "server_shutting_down",
            "daemon is shutting down",
            serde_json::json!({}),
        ),
    };
    let body = serde_json::to_vec(&sandbox_operation_contract::error_response_with_details(
        kind, message, details,
    ))
    .unwrap_or_default();
    let head = format!(
        "HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(head.as_bytes()).await;
    let _ = stream.write_all(&body).await;
    let _ = stream.flush().await;

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
    let child_tasks = TaskTracker::new();
    let service_tasks = child_tasks.clone();
    let shutdown = state.shutdown.clone();
    let service = service_fn(move |req: Request<Incoming>| {
        let state = Arc::clone(&state);
        let child_tasks = service_tasks.clone();
        async move { Ok::<Response<BoxBody>, Infallible>(router::route(state, child_tasks, req).await) }
    });
    let connection = hyper::server::conn::http1::Builder::new()
        .serve_connection(TokioIo::new(stream), service)
        .with_upgrades();
    tokio::select! {
        () = shutdown.cancelled() => {}
        _ = connection => {}
    }
    child_tasks.close();
    child_tasks.wait().await;
}
