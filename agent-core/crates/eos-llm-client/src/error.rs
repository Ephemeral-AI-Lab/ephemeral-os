//! The single `thiserror` error enum for the provider boundary.
//!
//! Per spec-conventions §8 this crate owns exactly one error type. The Python
//! `EphemeralOSApiError` subclass hierarchy (`AuthenticationFailure`,
//! `RateLimitFailure`, `RequestFailure`) collapses into one [`ProviderError`]
//! struct carrying a [`ProviderErrorKind`], so callers branch on the typed kind
//! without re-deriving the category by message-substring matching
//! (`providers/errors.py`, `anthropic_native.py::_translate_error`/`_categorize`).

/// The category of a provider failure.
///
/// Derived from HTTP status by [`ProviderError::from_status`]. `Transport` and
/// `Decode` are SDK-free additions with no Python source: they let the retry
/// gate (`retry.rs`) treat a connect/timeout failure as retryable and a stream
/// parse failure as fatal, without inspecting a status code that does not exist
/// on those paths.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum ProviderErrorKind {
    /// 401/403 — credentials rejected (`AuthenticationFailure`).
    Authentication,
    /// 429 — upstream rate limit (`RateLimitFailure`).
    RateLimit,
    /// 500/502/503/529 — transient upstream server failure.
    Server,
    /// Other HTTP / generic request failure (`RequestFailure`).
    Request,
    /// `reqwest` connect/timeout — no HTTP status.
    Transport,
    /// SSE/JSON stream parse failure — no HTTP status.
    Decode,
}

/// A normalized upstream provider failure.
///
/// `request_id` is the provider's opaque HTTP `request-id`/`x-request-id` header
/// (`errors.py::EphemeralOSApiError.request_id`), **not** the internal
/// `eos_types::RequestId`. It is captured before the response body is consumed
/// so it survives the streaming error path.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{kind:?} provider error (status {status_code:?}, request {request_id:?}): {message}")]
#[non_exhaustive]
pub struct ProviderError {
    /// The failure category.
    pub kind: ProviderErrorKind,
    /// The HTTP status code, if the failure was HTTP-shaped.
    pub status_code: Option<u16>,
    /// The provider HTTP request-id header, if present.
    pub request_id: Option<String>,
    /// Lowercase, punctuation-free human description (`err-lowercase-msg`).
    pub message: String,
}

impl ProviderError {
    /// Map an HTTP status to a [`ProviderError`], preserving `status_code` and
    /// `request_id`.
    ///
    /// Combines `_translate_error` (401/403→`Authentication`, 429→`RateLimit`,
    /// else→`Request`) with `_categorize`'s 5xx grouping
    /// ({500,502,503,529}→`Server`). A 5xx outside that set (e.g. 504) maps to
    /// `Request`, matching the Python `unknown`/`RequestFailure` fall-through.
    #[must_use]
    pub fn from_status(
        status: u16,
        request_id: Option<String>,
        message: impl Into<String>,
    ) -> Self {
        let kind = match status {
            401 | 403 => ProviderErrorKind::Authentication,
            429 => ProviderErrorKind::RateLimit,
            500 | 502 | 503 | 529 => ProviderErrorKind::Server,
            _ => ProviderErrorKind::Request,
        };
        Self {
            kind,
            status_code: Some(status),
            request_id,
            message: message.into(),
        }
    }

    /// A connect/timeout transport failure with no HTTP status.
    #[must_use]
    pub fn transport(message: impl Into<String>) -> Self {
        Self {
            kind: ProviderErrorKind::Transport,
            status_code: None,
            request_id: None,
            message: message.into(),
        }
    }

    /// A stream/JSON decode failure with no HTTP status.
    #[must_use]
    pub fn decode(request_id: Option<String>, message: impl Into<String>) -> Self {
        Self {
            kind: ProviderErrorKind::Decode,
            status_code: None,
            request_id,
            message: message.into(),
        }
    }

    /// A synchronous request-construction failure (URL/header/body build) — the
    /// only failure surfaced as the outer `Err` of `stream_message` (§5).
    #[must_use]
    pub fn request(message: impl Into<String>) -> Self {
        Self {
            kind: ProviderErrorKind::Request,
            status_code: None,
            request_id: None,
            message: message.into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // AC-llm-client-04: status→kind mapping preserves status_code + request_id.
    #[test]
    fn maps_status_to_kind_preserving_request_id() {
        let cases = [
            (401, ProviderErrorKind::Authentication),
            (403, ProviderErrorKind::Authentication),
            (429, ProviderErrorKind::RateLimit),
            (500, ProviderErrorKind::Server),
            (502, ProviderErrorKind::Server),
            (503, ProviderErrorKind::Server),
            (529, ProviderErrorKind::Server),
            (400, ProviderErrorKind::Request),
            (404, ProviderErrorKind::Request),
            (504, ProviderErrorKind::Request),
        ];
        for (status, expected) in cases {
            let err = ProviderError::from_status(status, Some("req-7".to_owned()), "boom");
            assert_eq!(err.kind, expected, "status {status}");
            assert_eq!(err.status_code, Some(status));
            assert_eq!(err.request_id.as_deref(), Some("req-7"));
        }
    }

    #[test]
    fn transport_and_decode_have_no_status() {
        let t = ProviderError::transport("connection reset");
        assert_eq!(t.kind, ProviderErrorKind::Transport);
        assert_eq!(t.status_code, None);

        let d = ProviderError::decode(Some("req-9".to_owned()), "bad frame");
        assert_eq!(d.kind, ProviderErrorKind::Decode);
        assert_eq!(d.status_code, None);
        assert_eq!(d.request_id.as_deref(), Some("req-9"));
    }
}
