//! The console HTTP listener: a loopback TCP accept loop serving each
//! connection over HTTP/1.1 with upgrade support so the preview proxy can
//! tunnel WebSockets.

use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;

use http::{Request, Response};
use hyper::body::Incoming;
use hyper::service::service_fn;
use hyper_util::rt::TokioIo;
use tokio::net::{TcpListener, TcpStream};

use crate::response::BoxBody;
use crate::router;
use crate::state::AppState;

/// Bind the console listener and serve until the process exits.
///
/// # Errors
/// Returns an error when the bind address is invalid or the listener cannot
/// be bound.
pub async fn run(state: AppState) -> std::io::Result<()> {
    let listener = TcpListener::bind(state.config.bind.as_str()).await?;
    let local = listener.local_addr()?;
    println!(
        "sandbox-console listening on http://{local} (gateway {})",
        state.config.gateway.gateway_socket_path.display()
    );
    serve(listener, state).await
}

/// Serve console connections from an already-bound listener until the
/// process exits.
///
/// # Errors
/// Never returns `Ok`; the error type keeps the signature aligned with
/// [`run`].
pub async fn serve(listener: TcpListener, state: AppState) -> std::io::Result<()> {
    let state = Arc::new(state);
    loop {
        let Ok((stream, peer)) = listener.accept().await else {
            continue;
        };
        tokio::spawn(serve_connection(stream, peer, Arc::clone(&state)));
    }
}

async fn serve_connection(stream: TcpStream, peer: SocketAddr, state: Arc<AppState>) {
    let service = service_fn(move |mut req: Request<Incoming>| {
        let state = Arc::clone(&state);
        req.extensions_mut().insert(peer);
        async move { Ok::<Response<BoxBody>, Infallible>(router::route(state, req).await) }
    });
    let _ = hyper::server::conn::http1::Builder::new()
        .serve_connection(TokioIo::new(stream), service)
        .with_upgrades()
        .await;
}
