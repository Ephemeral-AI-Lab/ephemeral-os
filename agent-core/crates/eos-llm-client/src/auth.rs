//! Explicit provider authentication.
//!
//! Source: `providers/auth_strategy.py` — only the explicit-credential shape
//! survives. The base-url heuristic (`use_auth_token = bool(base_url) and
//! "anthropic.com" not in base_url`) and the macOS credential-store OAuth
//! strategy are dropped (GC-llm-client-04, anchor §2). The caller picks the
//! scheme; this crate never inspects `base_url` to choose it.
//!
//! Credentials are held in [`secrecy::SecretString`] so they are redacted in
//! `Debug`/logs and never printed (plan reliability rule).

use reqwest::header::{HeaderMap, HeaderName, HeaderValue, AUTHORIZATION};
use secrecy::{ExposeSecret, SecretString};

use crate::error::ProviderError;

/// The `x-api-key` header name (Anthropic).
const X_API_KEY: HeaderName = HeaderName::from_static("x-api-key");

/// How to authenticate a provider request.
///
/// `PartialEq` is intentionally not derived: [`SecretString`] does not implement
/// it (constant-time-comparison hygiene), and equality on credentials is not a
/// meaningful operation. `Debug` is the `SecretString`-redacted form.
#[derive(Debug)]
#[non_exhaustive]
pub enum Auth {
    /// Anthropic-style `x-api-key: <key>` (the `_ApiKeyStrategy` default).
    ApiKey(SecretString),
    /// `Authorization: Bearer <key>` (`OpenAI` / non-Anthropic; the Python
    /// `use_auth_token` branch).
    Bearer(SecretString),
}

impl Auth {
    /// Construct an `x-api-key` credential.
    #[must_use]
    pub fn api_key(key: impl Into<String>) -> Self {
        Self::ApiKey(SecretString::new(key.into()))
    }

    /// Construct a `Bearer` credential.
    #[must_use]
    pub fn bearer(key: impl Into<String>) -> Self {
        Self::Bearer(SecretString::new(key.into()))
    }

    /// Apply the matching authentication header to `headers`.
    ///
    /// Returns [`ProviderError::request`] if the credential is not a valid
    /// header value (e.g. contains control characters). Crate-internal: the
    /// provider clients call this; the public surface is the `Auth` constructors
    /// passed into `AnthropicClient::new`/`OpenAiClient::new`.
    pub(crate) fn apply(&self, headers: &mut HeaderMap) -> Result<(), ProviderError> {
        match self {
            Self::ApiKey(secret) => {
                let mut value = HeaderValue::from_str(secret.expose_secret())
                    .map_err(|_| ProviderError::request("api key is not a valid header value"))?;
                value.set_sensitive(true);
                headers.insert(X_API_KEY, value);
            }
            Self::Bearer(secret) => {
                let mut value =
                    HeaderValue::from_str(&format!("Bearer {}", secret.expose_secret())).map_err(
                        |_| ProviderError::request("bearer token is not a valid header value"),
                    )?;
                value.set_sensitive(true);
                headers.insert(AUTHORIZATION, value);
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    // AC-llm-client-08: ApiKey sets x-api-key; Bearer sets Authorization: Bearer.
    #[test]
    fn auth_kind_sets_expected_header() {
        let mut h = HeaderMap::new();
        Auth::api_key("sk-anthropic").apply(&mut h).unwrap();
        assert_eq!(h.get("x-api-key").unwrap(), "sk-anthropic");
        assert!(h.get(AUTHORIZATION).is_none());

        let mut h = HeaderMap::new();
        Auth::bearer("sk-openai").apply(&mut h).unwrap();
        assert_eq!(h.get(AUTHORIZATION).unwrap(), "Bearer sk-openai");
        assert!(h.get("x-api-key").is_none());
    }

    // GC-llm-client-04: secret is redacted in Debug, never the raw key.
    #[test]
    fn debug_redacts_secret() {
        let rendered = format!("{:?}", Auth::api_key("sk-supersecret"));
        assert!(!rendered.contains("sk-supersecret"), "debug leaked secret");
    }

    #[test]
    fn header_values_are_marked_sensitive() {
        let mut h = HeaderMap::new();
        Auth::bearer("sk-openai").apply(&mut h).unwrap();
        assert!(h.get(AUTHORIZATION).unwrap().is_sensitive());
    }
}
