//! Explicit provider authentication.
//!
//! The caller picks the scheme explicitly. This crate never inspects `base_url`
//! to choose auth and never reads platform credential stores or cache files.
//!
//! Credentials are held in [`secrecy::SecretString`] so they are redacted in
//! `Debug`/logs and never printed (plan reliability rule).

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use reqwest::header::{HeaderMap, HeaderName, HeaderValue, AUTHORIZATION};
use secrecy::{ExposeSecret, SecretString};
use serde::Deserialize;

use crate::error::ProviderError;

/// The `x-api-key` header name (Anthropic).
const X_API_KEY: HeaderName = HeaderName::from_static("x-api-key");
/// The `ChatGPT` workspace/account header used by the Codex backend.
const CHATGPT_ACCOUNT_ID: HeaderName = HeaderName::from_static("chatgpt-account-id");
/// `FedRAMP` routing header used by first-party Codex auth for `FedRAMP` accounts.
const X_OPENAI_FEDRAMP: HeaderName = HeaderName::from_static("x-openai-fedramp");
/// The namespaced claim where `ChatGPT` account metadata is stored.
const OPENAI_AUTH_CLAIM: &str = "https://api.openai.com/auth";

#[derive(Debug, Deserialize)]
struct CodexAccessClaims {
    #[serde(rename = "https://api.openai.com/auth")]
    auth: Option<CodexAuthClaims>,
}

#[derive(Debug, Deserialize)]
struct CodexAuthClaims {
    chatgpt_account_id: Option<String>,
    #[serde(default)]
    chatgpt_account_is_fedramp: bool,
}

/// How to authenticate a provider request.
///
/// `PartialEq` is intentionally not derived: [`SecretString`] does not implement
/// it (constant-time-comparison hygiene), and equality on credentials is not a
/// meaningful operation. `Debug` is the `SecretString`-redacted form.
#[derive(Debug)]
#[non_exhaustive]
pub enum Auth {
    /// Anthropic-style `x-api-key: <key>`.
    ApiKey(SecretString),
    /// `Authorization: Bearer <key>`.
    Bearer(SecretString),
    /// ChatGPT-managed Codex access-token auth.
    CodexAccess {
        /// The access token sent as `Authorization: Bearer`.
        token: SecretString,
        /// The `ChatGPT` workspace/account id sent as `ChatGPT-Account-ID`.
        account_id: String,
        /// Whether the selected account must route through the `FedRAMP` edge.
        is_fedramp_account: bool,
    },
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

    /// Construct Codex access-token credentials from the token JWT claims.
    ///
    /// The token must carry the `ChatGPT` workspace/account id in the
    /// `https://api.openai.com/auth` claim. This function reads only the token
    /// value supplied by the caller; it does not load or parse any credential
    /// cache file.
    ///
    /// # Errors
    /// Returns [`ProviderError::request`] when the token is not JWT-shaped, the
    /// payload cannot be decoded, or the required account id claim is absent.
    pub fn codex_access_token_from_jwt(token: impl Into<String>) -> Result<Self, ProviderError> {
        let token = token.into();
        let claims = decode_codex_access_claims(&token)?;
        let auth = claims.auth.ok_or_else(|| {
            ProviderError::request(format!(
                "codex access token missing {OPENAI_AUTH_CLAIM} claim"
            ))
        })?;
        let account_id = auth
            .chatgpt_account_id
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| {
                ProviderError::request("codex access token missing chatgpt_account_id claim")
            })?;
        Ok(Self::CodexAccess {
            token: SecretString::new(token),
            account_id,
            is_fedramp_account: auth.chatgpt_account_is_fedramp,
        })
    }

    /// Apply the matching authentication header to `headers`.
    ///
    /// Returns [`ProviderError::request`] if the credential is not a valid
    /// header value (e.g. contains control characters). Crate-internal: the
    /// provider clients call this; the public surface is the `Auth` constructors
    /// passed into `AnthropicApiClient::new`/`OpenAiApiClient::new`.
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
            Self::CodexAccess {
                token,
                account_id,
                is_fedramp_account,
            } => {
                let mut value = HeaderValue::from_str(&format!("Bearer {}", token.expose_secret()))
                    .map_err(|_| {
                        ProviderError::request("codex access token is not a valid header value")
                    })?;
                value.set_sensitive(true);
                headers.insert(AUTHORIZATION, value);

                let value = HeaderValue::from_str(account_id).map_err(|_| {
                    ProviderError::request("codex account id is not a valid header value")
                })?;
                headers.insert(CHATGPT_ACCOUNT_ID, value);

                if *is_fedramp_account {
                    headers.insert(X_OPENAI_FEDRAMP, HeaderValue::from_static("true"));
                }
            }
        }
        Ok(())
    }
}

fn decode_codex_access_claims(token: &str) -> Result<CodexAccessClaims, ProviderError> {
    let payload = token
        .split('.')
        .nth(1)
        .filter(|part| !part.is_empty())
        .ok_or_else(|| ProviderError::request("codex access token is not a jwt"))?;
    let decoded = URL_SAFE_NO_PAD
        .decode(payload)
        .map_err(|_| ProviderError::request("codex access token payload is not base64url"))?;
    serde_json::from_slice(&decoded)
        .map_err(|_| ProviderError::request("codex access token payload is not json"))
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

        let mut h = HeaderMap::new();
        let token = jwt_with_auth_claim(Some("account-123"), true);
        let expected_auth = format!("Bearer {token}");
        Auth::codex_access_token_from_jwt(token)
            .unwrap()
            .apply(&mut h)
            .unwrap();
        assert_eq!(
            h.get(AUTHORIZATION).unwrap().to_str().unwrap(),
            expected_auth
        );
        assert_eq!(h.get("chatgpt-account-id").unwrap(), "account-123");
        assert_eq!(h.get("x-openai-fedramp").unwrap(), "true");
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

    fn jwt_with_auth_claim(account_id: Option<&str>, fedramp: bool) -> String {
        let auth = match account_id {
            Some(account_id) => serde_json::json!({
                "chatgpt_account_id": account_id,
                "chatgpt_account_is_fedramp": fedramp,
            }),
            None => serde_json::json!({}),
        };
        let payload = serde_json::json!({ OPENAI_AUTH_CLAIM: auth });
        format!(
            "header.{}.signature",
            URL_SAFE_NO_PAD.encode(payload.to_string())
        )
    }

    #[test]
    fn codex_access_token_from_jwt_reads_account_claim() {
        let token = jwt_with_auth_claim(Some("account-123"), true);
        let mut h = HeaderMap::new();

        Auth::codex_access_token_from_jwt(token)
            .expect("auth")
            .apply(&mut h)
            .expect("headers");

        assert_eq!(h.get("chatgpt-account-id").unwrap(), "account-123");
        assert_eq!(h.get("x-openai-fedramp").unwrap(), "true");
    }

    #[test]
    fn codex_access_token_from_jwt_rejects_missing_account_claim() {
        let err = Auth::codex_access_token_from_jwt(jwt_with_auth_claim(None, false))
            .expect_err("missing account id should fail");
        assert!(err.message.contains("chatgpt_account_id"));
    }
}
