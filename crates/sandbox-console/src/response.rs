//! Shared response body type and status/header/body builders. Fixed buffers
//! and streamed proxy bodies share one boxed body type at the listener
//! boundary.

use bytes::Bytes;
use http::header::{HeaderValue, CACHE_CONTROL, CONTENT_TYPE};
use http::{Response, StatusCode};
use http_body_util::{BodyExt as _, Full};
use serde_json::Value;

pub type BoxBody = http_body_util::combinators::BoxBody<Bytes, hyper::Error>;

pub fn full(bytes: impl Into<Bytes>) -> BoxBody {
    Full::new(bytes.into())
        .map_err(|never| match never {})
        .boxed()
}

pub fn empty() -> BoxBody {
    full(Bytes::new())
}

pub fn text(status: StatusCode, message: &str) -> Response<BoxBody> {
    with_content_type(status, "text/plain; charset=utf-8", message.to_owned())
}

pub fn json_value(status: StatusCode, value: &Value) -> Response<BoxBody> {
    let body = serde_json::to_vec(value).unwrap_or_default();
    with_content_type(status, "application/json", body)
}

pub fn html(status: StatusCode, body: impl Into<Bytes>) -> Response<BoxBody> {
    with_content_type(status, "text/html; charset=utf-8", body)
}

pub fn no_store(mut response: Response<BoxBody>) -> Response<BoxBody> {
    response
        .headers_mut()
        .insert(CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

fn with_content_type(
    status: StatusCode,
    content_type: &'static str,
    body: impl Into<Bytes>,
) -> Response<BoxBody> {
    let mut response = Response::new(full(body.into()));
    *response.status_mut() = status;
    response
        .headers_mut()
        .insert(CONTENT_TYPE, HeaderValue::from_static(content_type));
    response
}
