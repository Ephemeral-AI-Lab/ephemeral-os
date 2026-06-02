//! The visible-output retry gate.
//!
//! Source: `anthropic_native.py::stream_message` retry loop, unified onto
//! `eos_config::RetryConfig` (the crate keeps no local retry constants —
//! GC-eos-config-04). [`retry_stream`] is an **outer** generator that re-invokes
//! a per-attempt stream factory across retries while tracking a single
//! `emitted_visible` flag: once any visible event (`AssistantTextDelta` /
//! `ReasoningDelta` / `ToolUseDelta`) is forwarded, a later failure fails fast
//! (re-running would duplicate text deltas and double-dispatch `tool_use_id`s
//! downstream — plan §"API Client Layer"). The Python refresh-on-401 retry is
//! dropped with the OAuth strategy.

use std::time::Duration;

use eos_config::RetryConfig;
use futures::future::BoxFuture;
use futures::StreamExt;

use crate::client::LlmStream;
use crate::error::{ProviderError, ProviderErrorKind};
use crate::events::LlmStreamEvent;

/// Wrap a per-attempt factory in the retry gate, returning a single linear
/// [`LlmStream`]. `cfg` is taken **by value**: the returned stream is `'static`
/// and cannot borrow the caller's config for its lifetime.
pub(crate) fn retry_stream<F>(cfg: RetryConfig, mut factory: F) -> LlmStream
where
    F: FnMut() -> BoxFuture<'static, Result<LlmStream, ProviderError>> + Send + 'static,
{
    Box::pin(async_stream::stream! {
        let mut emitted_visible = false;
        let mut attempt: u32 = 0;
        loop {
            match factory().await {
                Err(err) => {
                    if can_retry(&cfg, &err, emitted_visible, attempt) {
                        backoff(&cfg, attempt).await;
                        attempt += 1;
                        continue;
                    }
                    yield Err(err);
                    return;
                }
                Ok(stream) => {
                    futures::pin_mut!(stream);
                    let mut completed = true;
                    while let Some(item) = stream.next().await {
                        match item {
                            Ok(event) => {
                                if is_visible(&event) {
                                    emitted_visible = true;
                                }
                                yield Ok(event);
                            }
                            Err(err) => {
                                if can_retry(&cfg, &err, emitted_visible, attempt) {
                                    completed = false;
                                    break;
                                }
                                yield Err(err);
                                return;
                            }
                        }
                    }
                    if completed {
                        // Stream ended cleanly (AssistantMessageComplete or an
                        // empty stream); nothing more to do.
                        return;
                    }
                }
            }
            // Reached only on a retryable failure with budget remaining.
            backoff(&cfg, attempt).await;
            attempt += 1;
        }
    })
}

/// Whether a fresh attempt is permitted: no visible output yet, budget left, and
/// a retryable kind (status-gated for HTTP).
fn can_retry(cfg: &RetryConfig, err: &ProviderError, emitted_visible: bool, attempt: u32) -> bool {
    !emitted_visible && attempt < cfg.max_retries && is_retryable(cfg, err)
}

/// Retryable iff `RateLimit`/`Server` with a status in `cfg.status_codes`, or a
/// `Transport` (connect/timeout) failure. `Decode`/`Authentication`/`Request`
/// are never retried.
fn is_retryable(cfg: &RetryConfig, err: &ProviderError) -> bool {
    match err.kind {
        ProviderErrorKind::RateLimit | ProviderErrorKind::Server => err
            .status_code
            .is_some_and(|s| cfg.status_codes.contains(&s)),
        ProviderErrorKind::Transport => true,
        ProviderErrorKind::Authentication
        | ProviderErrorKind::Request
        | ProviderErrorKind::Decode => false,
    }
}

/// Whether an event counts as visible output for the gate.
fn is_visible(event: &LlmStreamEvent) -> bool {
    matches!(
        event,
        LlmStreamEvent::AssistantTextDelta { .. }
            | LlmStreamEvent::ReasoningDelta { .. }
            | LlmStreamEvent::ToolUseDelta { .. }
    )
}

/// Sleep `min(base_delay_s * 2^attempt, max_delay_s)` (Python parity).
async fn backoff(cfg: &RetryConfig, attempt: u32) {
    let delay = (cfg.base_delay_s * 2f64.powi(attempt as i32)).min(cfg.max_delay_s);
    // Fallible construction: a non-finite or overflowing delay (reachable from an
    // unvalidated `+inf`/huge `max_delay_s`, since eos-config only rejects
    // negatives) must skip the sleep rather than panic (`err-result-over-panic`).
    if let Ok(duration) = Duration::try_from_secs_f64(delay) {
        if duration > Duration::ZERO {
            tokio::time::sleep(duration).await;
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use std::sync::{Arc, Mutex};

    use eos_types::JsonObject;
    use futures::StreamExt;

    use super::*;
    use crate::events::LlmStreamEvent;

    /// One scripted attempt outcome.
    enum Attempt {
        /// Opening the attempt fails before any stream (connect / status).
        OpenErr(ProviderError),
        /// The attempt opens and yields these items.
        Stream(Vec<Result<LlmStreamEvent, ProviderError>>),
    }

    /// Build a `Send + 'static` factory that replays scripted attempts in order
    /// and counts its invocations.
    fn scripted(
        attempts: Vec<Attempt>,
    ) -> (
        impl FnMut() -> BoxFuture<'static, Result<LlmStream, ProviderError>> + Send + 'static,
        Arc<Mutex<usize>>,
    ) {
        let calls = Arc::new(Mutex::new(0usize));
        let calls_ret = Arc::clone(&calls);
        let script = Arc::new(Mutex::new(attempts.into_iter().collect::<Vec<_>>()));
        let factory = move || {
            *calls.lock().unwrap() += 1;
            let mut script = script.lock().unwrap();
            let outcome = if script.is_empty() {
                Attempt::OpenErr(ProviderError::transport("script exhausted"))
            } else {
                script.remove(0)
            };
            let fut: BoxFuture<'static, Result<LlmStream, ProviderError>> = match outcome {
                Attempt::OpenErr(e) => Box::pin(async move { Err(e) }),
                Attempt::Stream(items) => Box::pin(async move {
                    let s: LlmStream = Box::pin(futures::stream::iter(items));
                    Ok(s)
                }),
            };
            fut
        };
        (factory, calls_ret)
    }

    fn fast_cfg() -> RetryConfig {
        // Zero delay so the retry loop does not actually sleep in tests.
        // `RetryConfig` is `#[non_exhaustive]`, so it is built by deserialization
        // rather than a struct literal.
        serde_json::from_value(serde_json::json!({
            "max_retries": 3,
            "base_delay_s": 0.0,
            "max_delay_s": 0.0,
            "status_codes": [429, 500, 502, 503, 529],
        }))
        .unwrap()
    }

    fn text(s: &str) -> LlmStreamEvent {
        LlmStreamEvent::AssistantTextDelta { text: s.into() }
    }

    fn complete() -> LlmStreamEvent {
        LlmStreamEvent::AssistantMessageComplete {
            message: crate::message::Message::from_user_text(""),
            usage: crate::types::UsageSnapshot::default(),
            stop_reason: None,
        }
    }

    // AC-llm-client-02 (fail-fast): after a visible delta an injected transport
    // error yields the delta then exactly one Err and ends; factory invoked once.
    #[tokio::test]
    async fn fails_fast_after_visible_output() {
        let (factory, calls) = scripted(vec![Attempt::Stream(vec![
            Ok(text("hello")),
            Err(ProviderError::transport("dropped")),
        ])]);
        let stream = retry_stream(fast_cfg(), factory);
        let items: Vec<_> = stream.collect().await;

        assert_eq!(items.len(), 2);
        assert!(matches!(
            items[0],
            Ok(LlmStreamEvent::AssistantTextDelta { .. })
        ));
        assert!(matches!(items[1], Err(ref e) if e.kind == ProviderErrorKind::Transport));
        assert_eq!(*calls.lock().unwrap(), 1, "no retry after visible output");
    }

    // AC-llm-client-02 (retry-then-succeed): a pre-visible RateLimit re-invokes
    // the factory; the caller sees a clean delta stream with no Err item.
    #[tokio::test]
    async fn retries_only_before_visible_output() {
        let (factory, calls) = scripted(vec![
            Attempt::Stream(vec![Err(ProviderError::from_status(
                429,
                None,
                "slow down",
            ))]),
            Attempt::OpenErr(ProviderError::from_status(503, None, "unavailable")),
            Attempt::Stream(vec![Ok(text("a")), Ok(text("b")), Ok(complete())]),
        ]);
        let stream = retry_stream(fast_cfg(), factory);
        let items: Vec<_> = stream.collect().await;

        assert!(items.iter().all(Result::is_ok), "no error should surface");
        assert_eq!(items.len(), 3);
        assert_eq!(*calls.lock().unwrap(), 3, "two retries then success");
    }

    #[tokio::test]
    async fn does_not_retry_non_retryable_open_error() {
        let (factory, calls) = scripted(vec![Attempt::OpenErr(ProviderError::from_status(
            401, None, "bad key",
        ))]);
        let stream = retry_stream(fast_cfg(), factory);
        let items: Vec<_> = stream.collect().await;

        assert_eq!(items.len(), 1);
        assert!(matches!(items[0], Err(ref e) if e.kind == ProviderErrorKind::Authentication));
        assert_eq!(*calls.lock().unwrap(), 1, "auth failure is not retried");
    }

    #[tokio::test]
    async fn exhausts_retry_budget_then_fails() {
        // max_retries = 3 → 4 attempts total, all 429, all pre-visible.
        let attempts = (0..5)
            .map(|_| Attempt::OpenErr(ProviderError::from_status(429, None, "rl")))
            .collect();
        let (factory, calls) = scripted(attempts);
        let stream = retry_stream(fast_cfg(), factory);
        let items: Vec<_> = stream.collect().await;

        assert_eq!(items.len(), 1);
        assert!(matches!(items[0], Err(ref e) if e.kind == ProviderErrorKind::RateLimit));
        assert_eq!(*calls.lock().unwrap(), 4, "1 initial + 3 retries");
    }

    #[tokio::test]
    async fn tool_use_delta_counts_as_visible() {
        let tool = LlmStreamEvent::ToolUseDelta {
            tool_use_id: "toolu_1".parse().unwrap(),
            name: "read".into(),
            input: JsonObject::new(),
        };
        let (factory, calls) = scripted(vec![Attempt::Stream(vec![
            Ok(tool),
            Err(ProviderError::from_status(503, None, "server")),
        ])]);
        let stream = retry_stream(fast_cfg(), factory);
        let items: Vec<_> = stream.collect().await;

        assert_eq!(items.len(), 2);
        assert!(items[1].is_err());
        assert_eq!(*calls.lock().unwrap(), 1, "tool_use delta blocks retry");
    }

    // A delay that overflows `Duration` (reachable from a huge/`+inf` max_delay_s
    // that eos-config does not reject) must skip the sleep, not panic the live
    // stream task (`err-result-over-panic`). Without `try_from_secs_f64` this
    // test would abort inside `backoff`.
    #[tokio::test]
    async fn backoff_does_not_panic_on_overflowing_delay() {
        let cfg: RetryConfig = serde_json::from_value(serde_json::json!({
            "max_retries": 1,
            "base_delay_s": f64::MAX,
            "max_delay_s": f64::MAX,
            "status_codes": [503],
        }))
        .unwrap();
        let (factory, calls) = scripted(vec![
            Attempt::OpenErr(ProviderError::from_status(503, None, "server")),
            Attempt::Stream(vec![Ok(text("ok"))]),
        ]);
        let items: Vec<_> = retry_stream(cfg, factory).collect().await;

        assert!(items.iter().all(Result::is_ok));
        assert_eq!(*calls.lock().unwrap(), 2, "retried once without panicking");
    }
}
