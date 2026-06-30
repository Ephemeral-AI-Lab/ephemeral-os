//! Status/header/body builders shared by the health and forward responders.
//!
//! Every daemon HTTP response carries a [`BoxBody`] so fixed and streamed bodies
//! share one type at the listener boundary.

use bytes::Bytes;
use http::header::CONTENT_TYPE;
use http::{HeaderValue, Response, StatusCode};
use http_body_util::{BodyExt as _, Full};

/// The single response body type: either a fixed buffer or a streamed proxy
/// body, erased behind one boxed `Body`.
pub(crate) type BoxBody = http_body_util::combinators::BoxBody<Bytes, hyper::Error>;

/// Box a fixed byte buffer as a response body.
pub(crate) fn full(bytes: impl Into<Bytes>) -> BoxBody {
    Full::new(bytes.into())
        .map_err(|never| match never {})
        .boxed()
}

/// An empty response body.
pub(crate) fn empty() -> BoxBody {
    full(Bytes::new())
}

/// A `text/plain` response with the given status (used for error replies).
pub(crate) fn text(status: StatusCode, message: &str) -> Response<BoxBody> {
    let mut response = Response::new(full(message.to_owned()));
    *response.status_mut() = status;
    response.headers_mut().insert(
        CONTENT_TYPE,
        HeaderValue::from_static("text/plain; charset=utf-8"),
    );
    response
}

/// An `application/json` response with the given status.
pub(crate) fn json(status: StatusCode, body: &'static str) -> Response<BoxBody> {
    let mut response = Response::new(full(Bytes::from_static(body.as_bytes())));
    *response.status_mut() = status;
    response
        .headers_mut()
        .insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
    response
}
