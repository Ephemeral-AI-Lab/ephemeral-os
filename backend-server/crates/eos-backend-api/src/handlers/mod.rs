//! Route handlers plus the small extractors/helpers they share.

pub mod sandboxes;
pub mod stats;
pub mod stream;
pub mod tasks;
pub mod user_requests;

use std::str::FromStr;

use axum::extract::{FromRequest, Request};
use axum::Json;
use serde::de::DeserializeOwned;
use serde::Deserialize;

use eos_backend_types::Page;

use crate::error::ApiError;

/// `?limit=&offset=` query parameters for the list routes.
#[derive(Debug, Deserialize)]
pub(crate) struct Pagination {
    limit: Option<u32>,
    offset: Option<u32>,
}

impl Pagination {
    /// Resolve into a clamped [`Page`] (defaults applied, `limit` bounded).
    pub(crate) fn page(&self) -> Page {
        Page::new(
            self.limit.unwrap_or(Page::DEFAULT_LIMIT),
            self.offset.unwrap_or(0),
        )
    }
}

/// Parse a path segment into a typed id, mapping a parse failure to `400` (a
/// malformed id is client input, never an internal error).
pub(crate) fn parse_id<T: FromStr>(raw: &str, what: &'static str) -> Result<T, ApiError> {
    T::from_str(raw).map_err(|_| ApiError::BadRequest(format!("invalid {what} id")))
}

/// `Json` extractor that maps a deserialize/`deny_unknown_fields` rejection to a
/// `400` with the (credential-free) parse message, instead of axum's default
/// `422`. This is what rejects v1-unsupported sandbox override fields.
#[derive(Debug)]
pub(crate) struct ValidatedJson<T>(pub(crate) T);

impl<T, S> FromRequest<S> for ValidatedJson<T>
where
    T: DeserializeOwned,
    S: Send + Sync,
{
    type Rejection = ApiError;

    async fn from_request(req: Request, state: &S) -> Result<Self, Self::Rejection> {
        let Json(value) = Json::<T>::from_request(req, state)
            .await
            .map_err(|rejection| ApiError::BadRequest(rejection.body_text()))?;
        Ok(Self(value))
    }
}
