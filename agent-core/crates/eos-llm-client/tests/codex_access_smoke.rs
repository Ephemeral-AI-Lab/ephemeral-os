//! Live Codex access-token smoke test.
//!
//! This intentionally reads only the typed `providers` config section from
//! `agent-core/config/{prd,local}.yml`. Local credentials belong in gitignored
//! `agent-core/config/local.yml`; this crate does not know about or parse any
//! credential cache file.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use eos_llm_client::{
    Auth, CodexCodingPlanClient, ConfiguredLlmClient, LlmClient, LlmRequest, LlmRequestDefaults,
    LlmStreamEvent, Message, ProviderError, ProviderKind, ProvidersConfig, ToolChoice, ToolSpec,
};
use eos_types::JsonObject;
use futures::StreamExt;
use serde_json::{json, Value};
use serde_yaml::{Mapping, Value as YamlValue};

const SMOKE_TOOL_NAME: &str = "codex_smoke_terminal";
const PROVIDER_EVENT_TIMEOUT: Duration = Duration::from_secs(60);

#[tokio::test]
async fn codex_access_token_gets_llm_client_response() -> Result<(), ProviderError> {
    let Some(providers) = load_codex_provider_config()? else {
        return Ok(());
    };
    providers
        .validate()
        .map_err(|e| ProviderError::request(format!("providers config is invalid: {e}")))?;
    let token = providers
        .codex_coding_plan
        .access_token
        .as_ref()
        .ok_or_else(|| {
            ProviderError::request("providers.codex_coding_plan.access_token is required")
        })?;

    let model = providers.active_model_registration().ok_or_else(|| {
        ProviderError::request("providers.codex_coding_plan.models.active is required")
    })?;
    let inner = Arc::new(CodexCodingPlanClient::new(
        &providers.codex_coding_plan.base_url,
        Auth::codex_access_token_from_jwt(token.expose_secret())?,
        Arc::new(providers.retry),
    )?);
    let client = ConfiguredLlmClient::new(
        inner,
        LlmRequestDefaults::from_model_kwargs(model.key(), &model.kwargs),
    );
    let request = LlmRequest::builder("")
        .system_prompt(
            "You are checking Codex access. Call the codex_smoke_terminal tool exactly once.",
        )
        .message(Message::from_user_text(
            "Call codex_smoke_terminal with an empty JSON object.",
        ))
        .tools(vec![ToolSpec::new(
            SMOKE_TOOL_NAME,
            "No-op terminal-style smoke test tool.",
            empty_object_schema(),
            None,
        )])
        .tool_choice(ToolChoice::Tool {
            name: SMOKE_TOOL_NAME.to_owned(),
        })
        .max_tokens(256)
        .build();

    let mut stream = client.stream_message(request).await?;
    let next = tokio::time::timeout(PROVIDER_EVENT_TIMEOUT, stream.next())
        .await
        .map_err(|_| ProviderError::request("timed out waiting for codex provider event"))?;
    let event =
        next.ok_or_else(|| ProviderError::request("codex provider stream ended without events"))??;
    match event {
        LlmStreamEvent::ToolUseDelta { name, .. } => {
            if name == SMOKE_TOOL_NAME {
                return Ok(());
            }
            Err(ProviderError::decode(
                None,
                format!("codex smoke returned unexpected tool {name}"),
            ))
        }
        LlmStreamEvent::AssistantTextDelta { .. }
        | LlmStreamEvent::ReasoningDelta { .. }
        | LlmStreamEvent::AssistantMessageComplete { .. } => Ok(()),
        _ => Ok(()),
    }
}

fn load_codex_provider_config() -> Result<Option<ProvidersConfig>, ProviderError> {
    let providers = load_providers_config()?;
    if providers.active != ProviderKind::CodexCodingPlan {
        return Ok(None);
    }
    Ok(Some(providers))
}

fn load_providers_config() -> Result<ProvidersConfig, ProviderError> {
    let mut merged = YamlValue::Mapping(Mapping::new());
    for path in [config_dir().join("prd.yml"), config_dir().join("local.yml")] {
        if let Some(doc) = read_yaml(&path)? {
            deep_merge(&mut merged, doc);
        }
    }
    let providers = merged
        .as_mapping()
        .and_then(|mapping| mapping.get(YamlValue::String("providers".to_owned())))
        .ok_or_else(|| ProviderError::request("config section 'providers' is missing"))?;
    serde_yaml::from_value(providers.clone())
        .map_err(|e| ProviderError::request(format!("loading providers config failed: {e}")))
}

fn config_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .map_or_else(|| PathBuf::from("config"), |root| root.join("config"))
}

fn read_yaml(path: &Path) -> Result<Option<YamlValue>, ProviderError> {
    if !path.exists() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(path)
        .map_err(|e| ProviderError::request(format!("reading config failed: {e}")))?;
    let doc: YamlValue = serde_yaml::from_str(&text)
        .map_err(|e| ProviderError::request(format!("parsing config yaml failed: {e}")))?;
    Ok((!doc.is_null()).then_some(doc))
}

fn deep_merge(base: &mut YamlValue, overlay: YamlValue) {
    match (base, overlay) {
        (YamlValue::Mapping(base_map), YamlValue::Mapping(overlay_map)) => {
            for (key, value) in overlay_map {
                match base_map.get_mut(&key) {
                    Some(existing) => deep_merge(existing, value),
                    None => {
                        base_map.insert(key, value);
                    }
                }
            }
        }
        (slot, overlay) => *slot = overlay,
    }
}

fn empty_object_schema() -> JsonObject {
    match json!({
        "type": "object",
        "properties": {},
        "additionalProperties": false,
    }) {
        Value::Object(schema) => schema,
        _ => JsonObject::new(),
    }
}
