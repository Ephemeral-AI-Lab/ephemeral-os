//! The [`LlmClient`] seam (DIP + LSP, anchor §6).
//!
//! Source: `providers/types.py::SupportsStreamingMessages`. Stored as
//! `Arc<dyn LlmClient>` at the `eos-runtime` composition root (heterogeneous:
//! Anthropic, `OpenAI`, mock), so it uses `#[async_trait]` — native
//! async-fn-in-trait is not yet `dyn`-safe — and returns a boxed [`LlmStream`].

use std::pin::Pin;

use futures::Stream;

use crate::error::ProviderError;
use crate::events::LlmStreamEvent;
use crate::types::LlmRequest;

/// A streamed model invocation: a single linear stream of normalized events or
/// errors. The retry gate (`retry.rs`) runs lazily inside this stream, so a
/// non-retryable failure on the first attempt surfaces as an `Err` **item**,
/// not as the caller's outer `Err`.
pub type LlmStream = Pin<Box<dyn Stream<Item = Result<LlmStreamEvent, ProviderError>> + Send>>;

/// The provider-neutral streaming client seam.
///
/// Implementors: `AnthropicClient`, `OpenAiClient`, and test mocks. Every event
/// is a neutral [`LlmStreamEvent`], so the implementors are substitutable (LSP).
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
