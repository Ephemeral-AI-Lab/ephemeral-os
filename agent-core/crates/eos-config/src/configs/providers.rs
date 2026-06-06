//! Provider runtime configuration. The retry defaults here are the single source
//! of truth consumed by `eos-llm-client`; the client crate keeps no local retry
//! constants.

use std::collections::BTreeSet;

use serde::{Deserialize, Deserializer, Serialize, Serializer};

use super::models::{ModelRegistrationConfig, ModelsConfig};
use crate::error::ConfigError;

/// Provider retry policy.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct RetryConfig {
    /// Maximum retry attempts.
    pub max_retries: u32,
    /// Initial backoff delay in seconds. Range-checked `>= 0`.
    pub base_delay_s: f64,
    /// Maximum backoff delay in seconds. Range-checked `>= 0`.
    pub max_delay_s: f64,
    /// HTTP status codes that trigger a retry. A [`BTreeSet`] (not a hash set)
    /// for deterministic serialized ordering.
    pub status_codes: BTreeSet<u16>,
}

impl Default for RetryConfig {
    fn default() -> Self {
        Self {
            max_retries: 3,
            base_delay_s: 1.0,
            max_delay_s: 30.0,
            status_codes: [429, 500, 502, 503, 529].into_iter().collect(),
        }
    }
}

impl RetryConfig {
    /// Enforce numeric-range constraints (call after deserializing a section).
    ///
    /// # Errors
    /// Returns [`ConfigError::OutOfRange`] when a delay is negative.
    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.base_delay_s < 0.0 || self.max_delay_s < 0.0 {
            return Err(ConfigError::OutOfRange {
                field: "providers.retry.*delay_s".to_owned(),
                detail: "must be >= 0".to_owned(),
            });
        }
        Ok(())
    }
}

/// A secret value loaded from gitignored local config.
#[derive(Clone, PartialEq, Eq)]
pub struct SecretConfigValue(String);

impl SecretConfigValue {
    /// Borrow the raw secret for the provider client constructor.
    #[must_use]
    pub fn expose_secret(&self) -> &str {
        &self.0
    }

    /// Whether this secret is blank after trimming whitespace.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.0.trim().is_empty()
    }
}

impl std::fmt::Debug for SecretConfigValue {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("SecretConfigValue(**redacted**)")
    }
}

impl Serialize for SecretConfigValue {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str("<redacted>")
    }
}

impl<'de> Deserialize<'de> for SecretConfigValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        String::deserialize(deserializer).map(Self)
    }
}

/// The selected LLM provider credential/client family.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum ProviderKind {
    /// No provider is selected; runtime uses the unconfigured placeholder.
    #[default]
    Unconfigured,
    /// `OpenAI` public API key through the Responses API.
    OpenAiApi,
    /// Anthropic public API key through the Messages API.
    AnthropicApi,
    /// ChatGPT-managed Codex coding-plan access token.
    CodexCodingPlan,
    /// Claude Code coding-plan access token.
    ClaudeCodingPlan,
}

/// `OpenAI` public API provider config.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct OpenAiApiConfig {
    /// Base URL for the public `OpenAI` API.
    #[serde(default = "default_openai_api_base_url")]
    pub base_url: String,
    /// API key sent as an Authorization bearer token.
    #[serde(default)]
    pub api_key: Option<SecretConfigValue>,
    /// Models available through this provider.
    #[serde(default)]
    pub models: ModelsConfig,
}

impl Default for OpenAiApiConfig {
    fn default() -> Self {
        Self {
            base_url: default_openai_api_base_url(),
            api_key: None,
            models: ModelsConfig::default(),
        }
    }
}

/// Anthropic public API provider config.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct AnthropicApiConfig {
    /// Base URL for the public Anthropic API.
    #[serde(default = "default_anthropic_api_base_url")]
    pub base_url: String,
    /// API key sent as `x-api-key`.
    #[serde(default)]
    pub api_key: Option<SecretConfigValue>,
    /// Models available through this provider.
    #[serde(default)]
    pub models: ModelsConfig,
}

impl Default for AnthropicApiConfig {
    fn default() -> Self {
        Self {
            base_url: default_anthropic_api_base_url(),
            api_key: None,
            models: ModelsConfig::default(),
        }
    }
}

/// Codex coding-plan provider config.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct CodexCodingPlanConfig {
    /// Base URL for the ChatGPT-backed Codex endpoint.
    #[serde(default = "default_codex_coding_plan_base_url")]
    pub base_url: String,
    /// ChatGPT-managed Codex access token JWT.
    #[serde(default)]
    pub access_token: Option<SecretConfigValue>,
    /// Models available through this provider.
    #[serde(default)]
    pub models: ModelsConfig,
}

impl Default for CodexCodingPlanConfig {
    fn default() -> Self {
        Self {
            base_url: default_codex_coding_plan_base_url(),
            access_token: None,
            models: ModelsConfig::default(),
        }
    }
}

/// Claude coding-plan provider config.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct ClaudeCodingPlanConfig {
    /// Base URL for the Anthropic OAuth-backed Messages endpoint.
    #[serde(default = "default_anthropic_api_base_url")]
    pub base_url: String,
    /// Claude coding-plan access token.
    #[serde(default)]
    pub access_token: Option<SecretConfigValue>,
    /// Models available through this provider.
    #[serde(default)]
    pub models: ModelsConfig,
}

impl Default for ClaudeCodingPlanConfig {
    fn default() -> Self {
        Self {
            base_url: default_anthropic_api_base_url(),
            access_token: None,
            models: ModelsConfig::default(),
        }
    }
}

/// Provider-level runtime configuration.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct ProvidersConfig {
    /// The one provider family selected for runtime model calls.
    #[serde(default)]
    pub active: ProviderKind,
    /// Retry policy applied across providers.
    #[serde(default)]
    pub retry: RetryConfig,
    /// `OpenAI` public API settings.
    #[serde(default)]
    pub openai_api: OpenAiApiConfig,
    /// Anthropic public API settings.
    #[serde(default)]
    pub anthropic_api: AnthropicApiConfig,
    /// ChatGPT-managed Codex coding-plan settings.
    #[serde(default)]
    pub codex_coding_plan: CodexCodingPlanConfig,
    /// Claude Code coding-plan settings.
    #[serde(default)]
    pub claude_coding_plan: ClaudeCodingPlanConfig,
}

impl Default for ProvidersConfig {
    fn default() -> Self {
        Self {
            active: ProviderKind::Unconfigured,
            retry: RetryConfig::default(),
            openai_api: OpenAiApiConfig::default(),
            anthropic_api: AnthropicApiConfig::default(),
            codex_coding_plan: CodexCodingPlanConfig::default(),
            claude_coding_plan: ClaudeCodingPlanConfig::default(),
        }
    }
}

impl ProvidersConfig {
    /// Borrow the model section for the active provider.
    #[must_use]
    pub fn active_models(&self) -> Option<&ModelsConfig> {
        match self.active {
            ProviderKind::Unconfigured => None,
            ProviderKind::OpenAiApi => Some(&self.openai_api.models),
            ProviderKind::AnthropicApi => Some(&self.anthropic_api.models),
            ProviderKind::CodexCodingPlan => Some(&self.codex_coding_plan.models),
            ProviderKind::ClaudeCodingPlan => Some(&self.claude_coding_plan.models),
        }
    }

    /// Return the active provider's active model registration, synthesizing it
    /// from the provider-local active key when no row is listed.
    #[must_use]
    pub fn active_model_registration(&self) -> Option<ModelRegistrationConfig> {
        self.active_models()?.active_registration()
    }

    /// Validate nested provider sections.
    ///
    /// # Errors
    /// Propagates [`RetryConfig::validate`] and returns
    /// [`ConfigError::MissingValue`] when the selected provider is missing a
    /// required provider field.
    pub fn validate(&self) -> Result<(), ConfigError> {
        self.retry.validate()?;
        self.openai_api
            .models
            .validate_at("providers.openai_api.models")?;
        self.anthropic_api
            .models
            .validate_at("providers.anthropic_api.models")?;
        self.codex_coding_plan
            .models
            .validate_at("providers.codex_coding_plan.models")?;
        self.claude_coding_plan
            .models
            .validate_at("providers.claude_coding_plan.models")?;
        match self.active {
            ProviderKind::Unconfigured => Ok(()),
            ProviderKind::OpenAiApi => {
                require_non_empty("providers.openai_api.base_url", &self.openai_api.base_url)?;
                require_secret(
                    "providers.openai_api.api_key",
                    self.openai_api.api_key.as_ref(),
                )?;
                require_active_model(
                    "providers.openai_api.models.active",
                    &self.openai_api.models,
                )
            }
            ProviderKind::AnthropicApi => {
                require_non_empty(
                    "providers.anthropic_api.base_url",
                    &self.anthropic_api.base_url,
                )?;
                require_secret(
                    "providers.anthropic_api.api_key",
                    self.anthropic_api.api_key.as_ref(),
                )?;
                require_active_model(
                    "providers.anthropic_api.models.active",
                    &self.anthropic_api.models,
                )
            }
            ProviderKind::CodexCodingPlan => {
                require_non_empty(
                    "providers.codex_coding_plan.base_url",
                    &self.codex_coding_plan.base_url,
                )?;
                require_secret(
                    "providers.codex_coding_plan.access_token",
                    self.codex_coding_plan.access_token.as_ref(),
                )?;
                require_active_model(
                    "providers.codex_coding_plan.models.active",
                    &self.codex_coding_plan.models,
                )
            }
            ProviderKind::ClaudeCodingPlan => {
                require_non_empty(
                    "providers.claude_coding_plan.base_url",
                    &self.claude_coding_plan.base_url,
                )?;
                require_secret(
                    "providers.claude_coding_plan.access_token",
                    self.claude_coding_plan.access_token.as_ref(),
                )?;
                require_active_model(
                    "providers.claude_coding_plan.models.active",
                    &self.claude_coding_plan.models,
                )
            }
        }
    }
}

fn default_openai_api_base_url() -> String {
    "https://api.openai.com".to_owned()
}

fn default_anthropic_api_base_url() -> String {
    "https://api.anthropic.com".to_owned()
}

fn default_codex_coding_plan_base_url() -> String {
    "https://chatgpt.com/backend-api/codex".to_owned()
}

fn require_non_empty(field: &str, value: &str) -> Result<(), ConfigError> {
    if value.trim().is_empty() {
        return Err(ConfigError::MissingValue {
            field: field.to_owned(),
        });
    }
    Ok(())
}

fn require_secret(field: &str, value: Option<&SecretConfigValue>) -> Result<(), ConfigError> {
    match value {
        Some(value) if !value.is_empty() => Ok(()),
        _ => Err(ConfigError::MissingValue {
            field: field.to_owned(),
        }),
    }
}

fn require_active_model(field: &str, models: &ModelsConfig) -> Result<(), ConfigError> {
    if models.active_key().is_some() {
        return Ok(());
    }
    Err(ConfigError::MissingValue {
        field: field.to_owned(),
    })
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]

    use super::*;

    #[test]
    fn defaults_are_set() {
        let r = RetryConfig::default();
        assert_eq!(r.max_retries, 3);
        assert_eq!(r.base_delay_s, 1.0);
        assert_eq!(r.max_delay_s, 30.0);
        assert_eq!(
            r.status_codes,
            [429u16, 500, 502, 503, 529].into_iter().collect()
        );
    }

    #[test]
    fn provider_defaults_are_unconfigured_and_non_secret() {
        let config = ProvidersConfig::default();
        assert_eq!(config.active, ProviderKind::Unconfigured);
        assert_eq!(config.openai_api.base_url, "https://api.openai.com");
        assert_eq!(config.anthropic_api.base_url, "https://api.anthropic.com");
        assert_eq!(
            config.claude_coding_plan.base_url,
            "https://api.anthropic.com"
        );
        assert_eq!(
            config.codex_coding_plan.base_url,
            "https://chatgpt.com/backend-api/codex"
        );
        assert!(config.openai_api.api_key.is_none());
        assert!(config.codex_coding_plan.access_token.is_none());
        config.validate().unwrap();
    }

    #[test]
    fn deserializes_claude_coding_plan_local_config() {
        let config: ProvidersConfig = serde_yaml::from_str(
            r#"
active: claude_coding_plan
claude_coding_plan:
  access_token: claude-oauth-token
  models:
    active: claude-sonnet-4-6
"#,
        )
        .unwrap();

        assert_eq!(config.active, ProviderKind::ClaudeCodingPlan);
        assert_eq!(
            config.claude_coding_plan.base_url,
            "https://api.anthropic.com"
        );
        assert_eq!(
            config
                .claude_coding_plan
                .access_token
                .as_ref()
                .unwrap()
                .expose_secret(),
            "claude-oauth-token"
        );
        assert_eq!(
            config.active_model_registration().unwrap().key(),
            "claude-sonnet-4-6"
        );
        config.validate().unwrap();
    }

    #[test]
    fn deserializes_codex_coding_plan_local_config() {
        let config: ProvidersConfig = serde_yaml::from_str(
            r#"
active: codex_coding_plan
codex_coding_plan:
  access_token: test.jwt.token
  models:
    active: gpt-5.5
    registrations:
      - key: gpt-5.5
        label: Codex GPT-5.5
        kwargs:
          reasoning_effort: medium
"#,
        )
        .unwrap();

        assert_eq!(config.active, ProviderKind::CodexCodingPlan);
        assert_eq!(
            config.codex_coding_plan.base_url,
            "https://chatgpt.com/backend-api/codex"
        );
        assert_eq!(
            config
                .codex_coding_plan
                .access_token
                .as_ref()
                .unwrap()
                .expose_secret(),
            "test.jwt.token"
        );
        let model = config.active_model_registration().unwrap();
        assert_eq!(model.key(), "gpt-5.5");
        assert_eq!(model.kwargs["reasoning_effort"], "medium");
        config.validate().unwrap();
    }

    #[test]
    fn active_provider_requires_secret() {
        let config = ProvidersConfig {
            active: ProviderKind::OpenAiApi,
            ..ProvidersConfig::default()
        };

        let err = config.validate().unwrap_err();
        assert!(matches!(
            err,
            ConfigError::MissingValue { field } if field == "providers.openai_api.api_key"
        ));
    }

    #[test]
    fn active_provider_requires_active_model() {
        let config: ProvidersConfig = serde_yaml::from_str(
            r#"
active: codex_coding_plan
codex_coding_plan:
  access_token: test.jwt.token
"#,
        )
        .unwrap();

        let err = config.validate().unwrap_err();
        assert!(matches!(
            err,
            ConfigError::MissingValue { field } if field == "providers.codex_coding_plan.models.active"
        ));
    }

    #[test]
    fn secret_debug_and_serialize_are_redacted() {
        let secret: SecretConfigValue = serde_yaml::from_str("sk-test").unwrap();

        assert_eq!(format!("{secret:?}"), "SecretConfigValue(**redacted**)");
        assert!(!serde_yaml::to_string(&secret).unwrap().contains("sk-test"));
    }
}
