//! The Anthropic Messages client: encode an [`LlmRequest`] to `/v1/messages`
//! (`stream: true`), decode the SSE response into normalized
//! [`LlmStreamEvent`]s, and wrap attempts in the retry gate.
//!
//! Source: `providers/clients/anthropic_native.py` — the official SDK is
//! replaced by direct `reqwest` + the `sse.rs` splitter. All provider projection
//! lives here (GC-llm-client-02): Anthropic encode drops `Reasoning` blocks and
//! `ToolSpec.output_schema`, and omits `metadata`/`is_terminal` from
//! `tool_result` wire bodies. Tool-use blocks are emitted mid-stream at
//! `content_block_stop` (the Rust advantage). Decode is a pure
//! frame-stream → event-stream function, independent of `reqwest`, so fixtures
//! replay through it with no HTTP.

use std::collections::HashMap;
use std::sync::Arc;

use bytes::Bytes;
use eos_types::ToolUseId;
use futures::future::BoxFuture;
use futures::{Stream, StreamExt};
use reqwest::header::{HeaderMap, HeaderName, HeaderValue, ACCEPT, CONTENT_TYPE, USER_AGENT};
use serde_json::{json, Value};

use crate::auth::Auth;
use crate::client::{build_endpoint, open_stream, LlmClient, LlmStream};
use crate::error::ProviderError;
use crate::events::{LlmStreamEvent, StopReason};
use crate::message::{ContentBlock, Message, MessageRole};
use crate::retry::retry_stream;
use crate::sse::{json_str, json_u32, json_usize, parse_sse_value, parse_tool_args};
use crate::types::{LlmRequest, ToolChoice, ToolSpec, UsageSnapshot};
use crate::RetryConfig;

/// The mandatory Anthropic API version header value.
const ANTHROPIC_VERSION: &str = "2023-06-01";
/// The Messages streaming endpoint path.
const MESSAGES_PATH: &str = "/v1/messages";
/// The Claude Code system identity prepended for OAuth-backed transport.
const CLAUDE_CODE_SYSTEM_PROMPT: &str = "You are Claude Code, Anthropic's official CLI for Claude.";

/// The Anthropic-native streaming client.
#[derive(Debug)]
pub struct AnthropicApiClient {
    http: reqwest::Client,
    endpoint: reqwest::Url,
    auth: Arc<Auth>,
    retry: Arc<RetryConfig>,
    extra_headers: HeaderMap,
    prepend_claude_code_system_prompt: bool,
}

impl AnthropicApiClient {
    /// Construct a client for `base_url` (e.g. `https://api.anthropic.com`).
    ///
    /// Returns the outer `Err` only on a malformed base url
    /// (`api-parse-dont-validate`).
    pub fn new(base_url: &str, auth: Auth, retry: Arc<RetryConfig>) -> Result<Self, ProviderError> {
        Self::new_with_options(base_url, auth, retry, HeaderMap::new(), false)
    }

    /// Construct a client for Claude coding-plan OAuth access tokens.
    pub(crate) fn new_claude_coding_plan(
        base_url: &str,
        auth: Auth,
        retry: Arc<RetryConfig>,
        beta_header: HeaderValue,
    ) -> Result<Self, ProviderError> {
        let mut extra_headers = HeaderMap::new();
        extra_headers.insert(HeaderName::from_static("anthropic-beta"), beta_header);
        extra_headers.insert(
            HeaderName::from_static("anthropic-dangerous-direct-browser-access"),
            HeaderValue::from_static("true"),
        );
        extra_headers.insert(USER_AGENT, HeaderValue::from_static("claude-cli/2.1.75"));
        extra_headers.insert(
            HeaderName::from_static("x-app"),
            HeaderValue::from_static("cli"),
        );
        Self::new_with_options(base_url, auth, retry, extra_headers, true)
    }

    fn new_with_options(
        base_url: &str,
        auth: Auth,
        retry: Arc<RetryConfig>,
        extra_headers: HeaderMap,
        prepend_claude_code_system_prompt: bool,
    ) -> Result<Self, ProviderError> {
        Ok(Self {
            http: reqwest::Client::new(),
            endpoint: build_endpoint(base_url, MESSAGES_PATH)?,
            auth: Arc::new(auth),
            retry,
            extra_headers,
            prepend_claude_code_system_prompt,
        })
    }

    fn build_headers(&self) -> Result<HeaderMap, ProviderError> {
        let mut headers = HeaderMap::new();
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers.insert(ACCEPT, HeaderValue::from_static("text/event-stream"));
        headers.insert(
            HeaderName::from_static("anthropic-version"),
            HeaderValue::from_static(ANTHROPIC_VERSION),
        );
        for (name, value) in &self.extra_headers {
            headers.insert(name, value.clone());
        }
        self.auth.apply(&mut headers)?;
        Ok(headers)
    }
}

#[async_trait::async_trait]
impl LlmClient for AnthropicApiClient {
    async fn stream_message(&self, request: LlmRequest) -> Result<LlmStream, ProviderError> {
        // Synchronous build — the only outer-`Err` path (§5).
        let body_value = if self.prepend_claude_code_system_prompt {
            encode_anthropic_body_with_options(&request, true)
        } else {
            encode_anthropic_body(&request)
        };
        let body = serde_json::to_vec(&body_value).map_err(|e| {
            ProviderError::request(format!("request body serialization failed: {e}"))
        })?;
        let body = Bytes::from(body);
        let headers = self.build_headers()?;
        let http = self.http.clone();
        let url = self.endpoint.clone();

        // Each attempt replays the owned bytes; connect/status/decode errors
        // surface as stream items, not the outer `Err`. The shared transport
        // plumbing lives in `client::open_stream`; only the decode differs.
        let factory = move || {
            let http = http.clone();
            let url = url.clone();
            let headers = headers.clone();
            let body = body.clone();
            Box::pin(open_stream(http, url, headers, body, |frames, rid| {
                decode_anthropic(frames, rid)
            })) as BoxFuture<'static, Result<LlmStream, ProviderError>>
        };
        Ok(retry_stream((*self.retry).clone(), factory))
    }
}

/// In-flight reassembly state for one content block.
#[derive(Debug)]
struct BlockAccum {
    block_type: String,
    id: String,
    name: String,
    text: String,
    input_json: String,
}

/// Decoder state across the whole message stream.
#[derive(Debug, Default)]
struct AnthropicState {
    blocks: HashMap<usize, BlockAccum>,
    content: Vec<ContentBlock>,
    input_tokens: u32,
    output_tokens: u32,
    stop_reason: Option<String>,
}

/// Decode an Anthropic Messages SSE frame stream into normalized events.
///
/// Pure: independent of `reqwest`, so fixtures replay through it. `input_tokens`
/// comes from `message_start`, `output_tokens` from `message_delta` (the SDK's
/// `get_final_message` merge). Malformed frame JSON logs (content-free) and ends
/// the stream with a `Decode` error stamped with `request_id` (§8.7, §8.8).
fn decode_anthropic<S>(
    frames: S,
    request_id: Option<String>,
) -> impl Stream<Item = Result<LlmStreamEvent, ProviderError>> + Send
where
    S: Stream<Item = Result<String, ProviderError>> + Send,
{
    async_stream::stream! {
        let mut state = AnthropicState::default();
        let mut frame_index: usize = 0;
        futures::pin_mut!(frames);
        while let Some(frame) = frames.next().await {
            frame_index += 1;
            let frame = match frame {
                Ok(frame) => frame,
                Err(err) => {
                    yield Err(err);
                    return;
                }
            };
            let value = match parse_sse_value(&frame, &request_id, "anthropic", frame_index) {
                Ok(Some(value)) => value,
                Ok(None) => continue,
                Err(err) => {
                    yield Err(err);
                    return;
                }
            };

            match value.get("type").and_then(Value::as_str) {
                Some("message_start") => {
                    state.input_tokens = json_u32(&value, &["message", "usage", "input_tokens"]);
                }
                Some("content_block_start") => {
                    let index = json_usize(&value, &["index"]);
                    let block = &value["content_block"];
                    state.blocks.insert(
                        index,
                        BlockAccum {
                            block_type: json_str(block, &["type"]),
                            id: json_str(block, &["id"]),
                            name: json_str(block, &["name"]),
                            text: String::new(),
                            input_json: String::new(),
                        },
                    );
                }
                Some("content_block_delta") => {
                    let index = json_usize(&value, &["index"]);
                    let delta = &value["delta"];
                    match delta.get("type").and_then(Value::as_str) {
                        Some("text_delta") => {
                            let text = json_str(delta, &["text"]);
                            if let Some(block) = state.blocks.get_mut(&index) {
                                block.text.push_str(&text);
                            }
                            yield Ok(LlmStreamEvent::AssistantTextDelta { text });
                        }
                        Some("thinking_delta") => {
                            let text = json_str(delta, &["thinking"]);
                            if let Some(block) = state.blocks.get_mut(&index) {
                                block.text.push_str(&text);
                            }
                            yield Ok(LlmStreamEvent::ReasoningDelta { text });
                        }
                        Some("input_json_delta") => {
                            let partial = json_str(delta, &["partial_json"]);
                            if let Some(block) = state.blocks.get_mut(&index) {
                                block.input_json.push_str(&partial);
                            }
                        }
                        _ => {}
                    }
                }
                Some("content_block_stop") => {
                    let index = json_usize(&value, &["index"]);
                    if let Some(block) = state.blocks.remove(&index) {
                        match block.block_type.as_str() {
                            "tool_use" => {
                                let input = parse_tool_args(&block.input_json);
                                // An empty/missing tool-use id is a malformed
                                // stream here, not a tolerated default. Rust
                                // passed the empty id through / synthesized a
                                // `toolu_<uuid>`, but the `ToolUseId` newtype
                                // rejects empty and the spec states default-id
                                // minting "lives in eos-types/engine, not here"
                                // (§6) — so this fails fast rather than minting
                                // or propagating an empty id. Anthropic always
                                // sends a `toolu_` id, so this never triggers.
                                let tool_use_id = match ToolUseId::try_from(block.id.as_str()) {
                                    Ok(id) => id,
                                    Err(_) => {
                                        yield Err(ProviderError::decode(
                                            request_id.clone(),
                                            "tool_use block missing id",
                                        ));
                                        return;
                                    }
                                };
                                state.content.push(ContentBlock::ToolUse {
                                    tool_use_id: tool_use_id.clone(),
                                    name: block.name.clone(),
                                    input: input.clone(),
                                });
                                yield Ok(LlmStreamEvent::ToolUseDelta {
                                    tool_use_id,
                                    name: block.name,
                                    input,
                                });
                            }
                            "text" => state.content.push(ContentBlock::Text { text: block.text }),
                            "thinking" => {
                                state.content.push(ContentBlock::Reasoning { text: block.text });
                            }
                            _ => {}
                        }
                    }
                }
                Some("message_delta") => {
                    if let Some(reason) = value["delta"].get("stop_reason").and_then(Value::as_str) {
                        state.stop_reason = Some(reason.to_owned());
                    }
                    state.output_tokens = json_u32(&value, &["usage", "output_tokens"]);
                }
                Some("message_stop") => {
                    yield Ok(LlmStreamEvent::AssistantMessageComplete {
                        message: Message {
                            role: MessageRole::Assistant,
                            content: std::mem::take(&mut state.content),
                        },
                        usage: UsageSnapshot {
                            input_tokens: state.input_tokens,
                            output_tokens: state.output_tokens,
                        },
                        stop_reason: state.stop_reason.as_deref().map(StopReason::parse),
                    });
                    return;
                }
                _ => {}
            }
        }
    }
}

/// Encode an [`LlmRequest`] into an Anthropic `/v1/messages` request body.
pub(crate) fn encode_anthropic_body(request: &LlmRequest) -> Value {
    encode_anthropic_body_with_options(request, false)
}

fn encode_anthropic_body_with_options(
    request: &LlmRequest,
    prepend_claude_code_system_prompt: bool,
) -> Value {
    let messages: Vec<Value> = request
        .messages
        .iter()
        .map(|message| {
            let content: Vec<Value> = message
                .content
                .iter()
                .filter_map(serialize_anthropic_block)
                .collect();
            json!({ "role": message.role.as_wire(), "content": content })
        })
        .collect();

    let mut body = json!({
        "model": request.model,
        "messages": messages,
        "max_tokens": request.max_tokens,
        "stream": true,
    });
    if prepend_claude_code_system_prompt {
        let mut system = vec![json!({
            "type": "text",
            "text": CLAUDE_CODE_SYSTEM_PROMPT,
        })];
        if let Some(prompt) = &request.system_prompt {
            system.push(json!({
                "type": "text",
                "text": prompt,
            }));
        }
        body["system"] = Value::Array(system);
    } else if let Some(system) = &request.system_prompt {
        body["system"] = json!(system);
    }
    if !request.tools.is_empty() {
        body["tools"] = Value::Array(request.tools.iter().map(serialize_anthropic_tool).collect());
    }
    if let Some(choice) = &request.tool_choice {
        body["tool_choice"] = encode_anthropic_tool_choice(choice);
    }
    body
}

/// Project one neutral block to the Anthropic wire shape. `Reasoning` is
/// provider-managed and skipped; `tool_result` omits `metadata`/`is_terminal`
/// (§8.6). Future neutral blocks are skipped until the provider projection owns
/// an explicit mapping.
fn serialize_anthropic_block(block: &ContentBlock) -> Option<Value> {
    match block {
        ContentBlock::Text { text } => Some(json!({ "type": "text", "text": text })),
        ContentBlock::ToolUse {
            tool_use_id,
            name,
            input,
        } => Some(json!({ "type": "tool_use", "id": tool_use_id, "name": name, "input": input })),
        ContentBlock::ToolResult {
            tool_use_id,
            content,
            is_error,
            ..
        } => Some(json!({
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
            "is_error": is_error,
        })),
        ContentBlock::SystemNotification { text } => Some(json!({
            "type": "text",
            "text": format!("<system-reminder>\n{text}\n</system-reminder>"),
        })),
        ContentBlock::Reasoning { .. } => None,
        _ => None,
    }
}

/// Project a tool spec to an Anthropic tool entry, dropping `output_schema`.
fn serialize_anthropic_tool(spec: &ToolSpec) -> Value {
    json!({
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
    })
}

fn encode_anthropic_tool_choice(choice: &ToolChoice) -> Value {
    match choice {
        ToolChoice::Auto => json!({ "type": "auto" }),
        ToolChoice::Any => json!({ "type": "any" }),
        ToolChoice::Tool { name } => json!({ "type": "tool", "name": name }),
    }
}

/// Decode an Anthropic SSE frame stream to events for cross-provider
/// substitutability tests (AC-llm-client-10, exercised from
/// `openai_api_client.rs`).
#[cfg(test)]
#[allow(clippy::unwrap_used)]
pub(crate) async fn decode_anthropic_for_test(
    frames: impl Stream<Item = Result<String, ProviderError>> + Send,
) -> Vec<LlmStreamEvent> {
    decode_anthropic(frames, None)
        .collect::<Vec<_>>()
        .await
        .into_iter()
        .map(Result::unwrap)
        .collect()
}

#[cfg(test)]
#[path = "../../tests/clients/anthropic_api_client.rs"]
mod tests;
