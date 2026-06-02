//! `eos-llm-client` — the provider-neutral LLM vocabulary and the direct
//! HTTP/SSE clients that turn an [`LlmRequest`] into a stream of normalized
//! [`LlmStreamEvent`]s.
//!
//! This crate is the single boundary where a wire protocol (Anthropic Messages,
//! `OpenAI` Responses) is encoded from neutral types and decoded back into neutral
//! types. It owns [`Message`]/[`ContentBlock`], [`UsageSnapshot`], [`LlmRequest`],
//! [`LlmStreamEvent`], [`ProviderError`], [`ToolSpec`], and the [`LlmClient`]
//! seam (anchor §5). It depends on no provider SDK — direct `reqwest` + a
//! hand-rolled SSE splitter only — and owns no engine-domain events, tool
//! registry, or lifecycle policy. See
//! `docs/plans/backend_agent_core_rust_migration/impl-eos-llm-client.md`.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod anthropic;
mod auth;
mod client;
mod error;
mod events;
mod message;
mod openai;
mod retry;
mod sse;
mod types;

pub use anthropic::AnthropicClient;
pub use auth::Auth;
pub use client::{LlmClient, LlmStream};
pub use error::{ProviderError, ProviderErrorKind};
pub use events::{LlmStreamEvent, StopReason};
pub use message::{ContentBlock, Message, MessageRole};
pub use openai::OpenAiClient;
pub use types::{
    LlmRequest, LlmRequestBuilder, ToolChoice, ToolSpec, UsageSnapshot, DEFAULT_MAX_TOKENS,
};
