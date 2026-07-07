//! The export spool stream route (spec decision 19): `GET
//! /export/<export_id>` claims a sealed spool with the single-use token from
//! the authenticated `export_layerstack` start and streams its bytes as one
//! `application/octet-stream` response. Every rejection — unknown export,
//! bad token, expiry, reuse — is one uniform 404; a mid-stream read failure
//! ends the body early and the manager's Content-Length completeness gate
//! rejects the truncated stream.

use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

use bytes::Bytes;
use http::header::{HeaderValue, CONTENT_LENGTH, CONTENT_TYPE};
use http::{Method, Request, Response, StatusCode};
use http_body_util::BodyExt as _;
use hyper::body::{Body, Frame, Incoming};
use sandbox_protocol::{EXPORT_STREAM_PATH_PREFIX, EXPORT_STREAM_TOKEN_HEADER};
use sandbox_runtime::ClaimedExportStream;
use tokio::io::{AsyncRead, ReadBuf};

use super::response::{self, BoxBody};
use super::server::HttpState;

const STREAM_READ_BYTES: usize = 64 * 1024;

pub(crate) async fn handle(state: Arc<HttpState>, req: Request<Incoming>) -> Response<BoxBody> {
    if req.method() != Method::GET {
        return response::text(StatusCode::METHOD_NOT_ALLOWED, "use GET");
    }
    let Some(export_id) = export_route_id(req.uri().path()) else {
        return unavailable();
    };
    let Some(token) = header_token(&req) else {
        return unavailable();
    };
    let export_id = export_id.to_owned();
    let operations = Arc::clone(&state.operations);
    let claimed = tokio::task::spawn_blocking(move || {
        operations
            .layerstack
            .claim_export_stream(&export_id, &token)
    })
    .await;
    match claimed {
        Ok(Some(claimed)) => stream_response(claimed),
        Ok(None) | Err(_) => unavailable(),
    }
}

fn export_route_id(path: &str) -> Option<&str> {
    let export_id = path.strip_prefix(EXPORT_STREAM_PATH_PREFIX)?;
    if export_id.is_empty() || export_id.contains('/') {
        return None;
    }
    Some(export_id)
}

fn header_token(req: &Request<Incoming>) -> Option<String> {
    req.headers()
        .get(EXPORT_STREAM_TOKEN_HEADER)?
        .to_str()
        .ok()
        .map(str::to_owned)
}

fn unavailable() -> Response<BoxBody> {
    response::text(StatusCode::NOT_FOUND, "export stream unavailable")
}

fn stream_response(claimed: ClaimedExportStream) -> Response<BoxBody> {
    let total = claimed.total;
    let body = SpoolBody {
        file: tokio::fs::File::from_std(claimed.file),
    };
    let mut response = Response::new(body.boxed());
    response.headers_mut().insert(
        CONTENT_TYPE,
        HeaderValue::from_static("application/octet-stream"),
    );
    response
        .headers_mut()
        .insert(CONTENT_LENGTH, HeaderValue::from(total));
    response
}

/// Streams the claimed (already unlinked) spool fd as body frames. A read
/// error terminates the body early instead of erroring: the manager's
/// completeness gate (received == Content-Length) converts the truncation
/// into a clean abort.
struct SpoolBody {
    file: tokio::fs::File,
}

impl Body for SpoolBody {
    type Data = Bytes;
    type Error = hyper::Error;

    fn poll_frame(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<Frame<Self::Data>, Self::Error>>> {
        let this = self.get_mut();
        let mut buf = [0u8; STREAM_READ_BYTES];
        let mut read_buf = ReadBuf::new(&mut buf);
        match Pin::new(&mut this.file).poll_read(cx, &mut read_buf) {
            Poll::Pending => Poll::Pending,
            Poll::Ready(Ok(())) => {
                let filled = read_buf.filled();
                if filled.is_empty() {
                    Poll::Ready(None)
                } else {
                    Poll::Ready(Some(Ok(Frame::data(Bytes::copy_from_slice(filled)))))
                }
            }
            Poll::Ready(Err(_)) => Poll::Ready(None),
        }
    }
}
