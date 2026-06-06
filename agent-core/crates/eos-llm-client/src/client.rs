//! The [`LlmClient`] seam (DIP + LSP, anchor §6).
//!
//! Source: `providers/types.py::SupportsStreamingMessages`. Stored as
//! `Arc<dyn LlmClient>` at the `eos-runtime` composition root (heterogeneous:
//! Anthropic, `OpenAI`, mock), so it uses `#[async_trait]` — native
//! async-fn-in-trait is not yet `dyn`-safe — and returns a boxed [`LlmStream`].

use std::pin::Pin;

use bytes::Bytes;
use futures::stream::BoxStream;
use futures::{Stream, StreamExt};
use reqwest::header::HeaderMap;

use crate::error::ProviderError;
use crate::events::LlmStreamEvent;
use crate::sse::frame_stream;
use crate::types::LlmRequest;

/// A streamed model invocation: a single linear stream of normalized events or
/// errors. The retry gate (`retry.rs`) runs lazily inside this stream, so a
/// non-retryable failure on the first attempt surfaces as an `Err` **item**,
/// not as the caller's outer `Err`.
pub type LlmStream = Pin<Box<dyn Stream<Item = Result<LlmStreamEvent, ProviderError>> + Send>>;

/// The provider-neutral streaming client seam.
///
/// Implementors: `AnthropicApiClient`, `OpenAiApiClient`,
/// `CodexCodingPlanClient`, `ClaudeCodingPlanClient`, and test mocks. Every
/// event is a neutral [`LlmStreamEvent`], so the implementors are substitutable
/// (LSP).
#[async_trait::async_trait]
pub trait LlmClient: Send + Sync {
    /// Open a streaming model invocation.
    ///
    /// The outer `Err` is reserved for **synchronous request-construction
    /// failures only** (URL/header/body build). All connect, auth, rate-limit,
    /// transport, and decode errors — including a non-retryable failure on the
    /// very first attempt — surface as `Err` items of the returned stream.
    async fn stream_message(&self, request: LlmRequest) -> Result<LlmStream, ProviderError>;
}

/// Build a concrete endpoint `Url` from a base url and a path suffix, parsing
/// (not merely string-concatenating) so a malformed base fails fast as the outer
/// `Err` of `stream_message` (`api-parse-dont-validate`).
pub(crate) fn build_endpoint(base_url: &str, path: &str) -> Result<reqwest::Url, ProviderError> {
    let trimmed = base_url.trim_end_matches('/');
    reqwest::Url::parse(&format!("{trimmed}{path}"))
        .map_err(|e| ProviderError::request(format!("invalid base url: {e}")))
}

/// Capture the provider request-id (`request-id` or `x-request-id`) from the
/// response headers **before** the body is consumed, so it survives the
/// streaming error path (invariant §8.8).
pub(crate) fn extract_request_id(headers: &reqwest::header::HeaderMap) -> Option<String> {
    headers
        .get("request-id")
        .or_else(|| headers.get("x-request-id"))
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned)
}

/// Truncate a provider error body for inclusion in a [`ProviderError`] message
/// (bounded so a large HTML error page does not bloat the error).
pub(crate) fn error_detail(body: &str) -> String {
    let trimmed = body.trim();
    if trimmed.is_empty() {
        return "no response body".to_owned();
    }
    trimmed.chars().take(500).collect()
}

/// Open one streaming attempt — the shared transport/SSE plumbing for both
/// providers. POST the request, capture the request-id **before** the body is
/// consumed (§8.8), map a non-2xx status, stamp the request-id onto mid-stream
/// transport errors, then decode the SSE frames via the provider-specific
/// `decode` closure. This is plumbing, not projection: the per-provider encode
/// and SSE→event mapping stay under `clients/`.
pub(crate) async fn open_stream<D, R>(
    http: reqwest::Client,
    url: reqwest::Url,
    headers: HeaderMap,
    body: Bytes,
    decode: D,
) -> Result<LlmStream, ProviderError>
where
    D: FnOnce(BoxStream<'static, Result<String, ProviderError>>, Option<String>) -> R,
    R: Stream<Item = Result<LlmStreamEvent, ProviderError>> + Send + 'static,
{
    let response = http
        .post(url)
        .headers(headers)
        .body(body)
        .send()
        .await
        .map_err(|e| ProviderError::transport(format!("request send failed: {e}")))?;

    let request_id = extract_request_id(response.headers());
    let status = response.status();
    if !status.is_success() {
        let detail = response.text().await.unwrap_or_default();
        return Err(ProviderError::from_status(
            status.as_u16(),
            request_id,
            error_detail(&detail),
        ));
    }

    // Stamp the captured request-id onto mid-stream transport errors too, so it
    // survives the streaming error path (§8.8), not just decode errors.
    let rid = request_id.clone();
    let byte_stream = response.bytes_stream().map(move |chunk| {
        chunk.map_err(|e| {
            let mut err = ProviderError::transport(format!("stream read failed: {e}"));
            err.request_id = rid.clone();
            err
        })
    });
    Ok(Box::pin(decode(
        frame_stream(byte_stream).boxed(),
        request_id,
    )))
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use std::sync::Arc;

    use futures::StreamExt;

    use super::*;
    use crate::events::LlmStreamEvent;

    /// A scripted client that replays a fixed event sequence — proves the trait
    /// is object-safe behind `Arc<dyn LlmClient>` and substitutable.
    #[derive(Debug)]
    struct MockLlmClient {
        events: Vec<LlmStreamEvent>,
    }

    #[async_trait::async_trait]
    impl LlmClient for MockLlmClient {
        async fn stream_message(&self, _request: LlmRequest) -> Result<LlmStream, ProviderError> {
            let events = self.events.clone();
            Ok(Box::pin(futures::stream::iter(events.into_iter().map(Ok))))
        }
    }

    #[tokio::test]
    async fn mock_client_is_object_safe_and_streams() {
        let client: Arc<dyn LlmClient> = Arc::new(MockLlmClient {
            events: vec![LlmStreamEvent::AssistantTextDelta { text: "hi".into() }],
        });
        let stream = client
            .stream_message(LlmRequest::builder("m").build())
            .await
            .unwrap();
        let collected: Vec<_> = stream.collect().await;
        assert_eq!(collected.len(), 1);
        assert!(matches!(
            collected[0],
            Ok(LlmStreamEvent::AssistantTextDelta { .. })
        ));
    }
}
