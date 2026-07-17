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
use tokio_util::sync::CancellationToken;

use crate::rpc::ServerConfig;

use super::response::BoxBody;
use super::router;

/// Shared state every request handler reads: runtime session state for target
/// resolution and the observer for the forward span.
pub(crate) struct HttpState {
    pub(crate) config: ServerConfig,
    pub(crate) operations: Arc<SandboxRuntimeOperations>,
    pub(crate) observer: Observer,
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
    shutdown: CancellationToken,
) -> JoinHandle<()> {
    let state = Arc::new(HttpState {
        config,
        operations,
        observer,
    });
    tokio::spawn(accept_loop(listener, state, shutdown))
}

async fn accept_loop(listener: TcpListener, state: Arc<HttpState>, shutdown: CancellationToken) {
    loop {
        tokio::select! {
            () = shutdown.cancelled() => break,
            accepted = listener.accept() => {
                let Ok((stream, _peer)) = accepted else { continue };
                tokio::spawn(serve_connection(stream, Arc::clone(&state)));
            }
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
