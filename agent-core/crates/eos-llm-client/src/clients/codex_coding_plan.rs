//! The Codex coding-plan client.
//!
//! This is the explicit client surface for ChatGPT-managed Codex access tokens.
//! It reuses the `OpenAI` Responses projection internally because the live
//! backend speaks the same normalized streaming shape with a small request-body
//! dialect difference.

use std::sync::Arc;

use crate::auth::Auth;
use crate::client::{LlmClient, LlmStream};
use crate::error::ProviderError;
use crate::types::LlmRequest;
use crate::RetryConfig;

use super::openai_api_client::OpenAiApiClient;

/// ChatGPT-managed Codex coding-plan streaming client.
#[derive(Debug)]
pub struct CodexCodingPlanClient {
    inner: OpenAiApiClient,
}

impl CodexCodingPlanClient {
    /// Construct a Codex coding-plan client for `base_url`.
    ///
    /// The caller supplies [`Auth::codex_access_token_from_jwt`] credentials;
    /// this crate does not read any credential cache file.
    pub fn new(base_url: &str, auth: Auth, retry: Arc<RetryConfig>) -> Result<Self, ProviderError> {
        Ok(Self {
            inner: OpenAiApiClient::new_codex_backend(base_url, auth, retry)?,
        })
    }
}

#[async_trait::async_trait]
impl LlmClient for CodexCodingPlanClient {
    async fn stream_message(&self, request: LlmRequest) -> Result<LlmStream, ProviderError> {
        self.inner.stream_message(request).await
    }
}
