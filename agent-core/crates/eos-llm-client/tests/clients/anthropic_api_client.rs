#![allow(clippy::unwrap_used)]
use super::*;
use eos_types::JsonObject;
use tracing_test::traced_test;

use crate::sse::frame_stream;

async fn decode_fixture(raw: &str) -> Vec<Result<LlmStreamEvent, ProviderError>> {
    let bytes = Bytes::from(raw.to_owned());
    let byte_stream = futures::stream::iter(vec![Ok::<Bytes, ProviderError>(bytes)]);
    decode_anthropic(frame_stream(byte_stream), Some("req-test".to_owned()))
        .collect()
        .await
}

// NOTE (flagged in the coverage review): `decode_anthropic`'s
// `match value.get("type")` has no "error" arm, so an in-stream provider
// `error` frame falls through `_ => {}` — the decoder emits no completion and
// no `Err`. This is not a silent system-wide drop: `loop_.rs` turns the
// missing completion into a generic `EngineError::Internal("provider stream
// ended without assistant completion")`. So the real cost is DEGRADED
// DIAGNOSTICS (the provider's actual error text is lost), not a swallowed
// failure. This pins the decoder-level behavior; a fix that surfaces the
// provider error as an `Err` should update this test.
#[tokio::test]
async fn in_stream_error_frame_is_swallowed_at_decoder_level() {
    let sse = concat!(
        "event: content_block_start\n",
        "data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n",
        "\n",
        "event: content_block_delta\n",
        "data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"hi\"}}\n",
        "\n",
        "event: error\n",
        "data: {\"type\":\"error\",\"error\":{\"type\":\"overloaded_error\",\"message\":\"overloaded\"}}\n",
    );
    let results = decode_fixture(sse).await;
    // The text delta before the error still decodes.
    assert!(results.iter().any(|event| matches!(
        event,
        Ok(LlmStreamEvent::AssistantTextDelta { text }) if text == "hi"
    )));
    // At the decoder boundary the error frame yields neither an `Err` nor a
    // terminal completion (the loop later maps the missing completion to a
    // generic stream-ended error).
    assert!(
        results.iter().all(Result::is_ok),
        "the decoder does not surface the provider error frame as an Err"
    );
    assert!(
        !results
            .iter()
            .any(|event| matches!(event, Ok(LlmStreamEvent::AssistantMessageComplete { .. }))),
        "no completion is emitted for an error-terminated stream"
    );
}

// AC-llm-client-01: full fixture decodes to reasoning/text deltas, then a
// mid-stream tool_use delta with parsed args, then a complete with correct
// usage (input from message_start, output from message_delta) + stop reason.
#[tokio::test]
async fn decodes_anthropic_sse_fixture() {
    let events: Vec<LlmStreamEvent> =
        decode_fixture(include_str!("../../tests/fixtures/anthropic/full.sse"))
            .await
            .into_iter()
            .map(Result::unwrap)
            .collect();

    assert_eq!(events.len(), 5);
    assert_eq!(
        events[0],
        LlmStreamEvent::ReasoningDelta {
            text: "Let me think".into()
        }
    );
    assert_eq!(
        events[1],
        LlmStreamEvent::AssistantTextDelta {
            text: "Hello".into()
        }
    );
    assert_eq!(
        events[2],
        LlmStreamEvent::AssistantTextDelta {
            text: " world".into()
        }
    );

    match &events[3] {
        LlmStreamEvent::ToolUseDelta {
            tool_use_id,
            name,
            input,
        } => {
            assert_eq!(tool_use_id.as_str(), "toolu_01");
            assert_eq!(name, "read_file");
            assert_eq!(input.get("path").and_then(Value::as_str), Some("foo.txt"));
        }
        other => panic!("expected tool_use delta, got {other:?}"),
    }

    match &events[4] {
        LlmStreamEvent::AssistantMessageComplete {
            message,
            usage,
            stop_reason,
        } => {
            assert_eq!(usage.input_tokens, 10);
            assert_eq!(usage.output_tokens, 15);
            assert_eq!(*stop_reason, Some(StopReason::ToolUse));
            assert_eq!(message.content.len(), 3);
            assert_eq!(message.assistant_text(), "Hello world");
            assert_eq!(message.reasoning_text(), "Let me think");
            assert_eq!(message.tool_uses().count(), 1);
        }
        other => panic!("expected complete, got {other:?}"),
    }
}

// AC-llm-client-03 (event half): an Anthropic `thinking_delta` event decodes
// to `LlmStreamEvent::ReasoningDelta` (the legacy "thinking" *block* half is
// proven in message.rs::reasoning_compat_decode_maps_thinking).
#[tokio::test]
async fn reasoning_compat_decode_maps_thinking_delta() {
    let sse = concat!(
        "event: content_block_start\n",
        "data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"thinking\",\"thinking\":\"\"}}\n",
        "\n",
        "event: content_block_delta\n",
        "data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"thinking_delta\",\"thinking\":\"reasoning step\"}}\n",
    );
    let events: Vec<LlmStreamEvent> = decode_fixture(sse)
        .await
        .into_iter()
        .map(Result::unwrap)
        .collect();
    assert_eq!(
        events,
        vec![LlmStreamEvent::ReasoningDelta {
            text: "reasoning step".into()
        }]
    );
}

// AC-llm-client-06 (anthropic side): encode drops output_schema and drops
// Reasoning content blocks.
#[test]
fn encode_projects_tools_per_provider() {
    let mut input_schema = JsonObject::new();
    input_schema.insert("type".into(), json!("object"));
    let mut output_schema = JsonObject::new();
    output_schema.insert("type".into(), json!("string"));
    let spec = ToolSpec::new(
        "read_file",
        "Read a file",
        input_schema,
        Some(output_schema),
    );

    let message = Message {
        role: MessageRole::Assistant,
        content: vec![
            ContentBlock::Reasoning {
                text: "private".into(),
            },
            ContentBlock::Text { text: "hi".into() },
        ],
    };
    let request = LlmRequest::builder("claude")
        .message(message)
        .tools(vec![spec])
        .system_prompt("sys")
        .build();
    let body = encode_anthropic_body(&request);

    let tool = &body["tools"][0];
    assert_eq!(tool["name"], json!("read_file"));
    assert!(tool.get("input_schema").is_some());
    assert!(
        tool.get("output_schema").is_none(),
        "anthropic drops output_schema"
    );

    let content = body["messages"][0]["content"].as_array().unwrap();
    assert_eq!(
        content.len(),
        1,
        "reasoning block dropped from wire messages"
    );
    assert_eq!(content[0]["type"], json!("text"));
    assert_eq!(content[0]["text"], json!("hi"));

    assert_eq!(body["stream"], json!(true));
    assert_eq!(body["system"], json!("sys"));
}

#[test]
fn tool_result_wire_omits_metadata_and_is_terminal() {
    let tuid: ToolUseId = "toolu_9".parse().unwrap();
    let mut metadata = JsonObject::new();
    metadata.insert("secret".into(), json!("nope"));
    let message = Message {
        role: MessageRole::User,
        content: vec![ContentBlock::ToolResult {
            tool_use_id: tuid,
            content: "ok".into(),
            is_error: false,
            metadata,
            is_terminal: true,
        }],
    };
    let body = encode_anthropic_body(&LlmRequest::builder("m").message(message).build());
    let block = &body["messages"][0]["content"][0];
    assert_eq!(block["type"], json!("tool_result"));
    assert!(
        block.get("metadata").is_none(),
        "metadata omitted from wire"
    );
    assert!(
        block.get("is_terminal").is_none(),
        "is_terminal omitted from wire"
    );
    assert_eq!(block["content"], json!("ok"));
}

#[test]
fn system_notification_wraps_in_reminder_tag() {
    let message = Message {
        role: MessageRole::User,
        content: vec![ContentBlock::SystemNotification {
            text: "stay on task".into(),
        }],
    };
    let body = encode_anthropic_body(&LlmRequest::builder("m").message(message).build());
    let block = &body["messages"][0]["content"][0];
    assert_eq!(block["type"], json!("text"));
    assert_eq!(
        block["text"],
        json!("<system-reminder>\nstay on task\n</system-reminder>")
    );
}

#[test]
fn claude_coding_plan_uses_oauth_transport_shape() {
    let client = AnthropicApiClient::new_claude_coding_plan(
        "https://api.anthropic.com",
        Auth::bearer("oauth-token"),
        Arc::new(RetryConfig::default()),
        HeaderValue::from_static("claude-code-20250219,oauth-2025-04-20"),
    )
    .unwrap();

    assert_eq!(
        client.endpoint.as_str(),
        "https://api.anthropic.com/v1/messages"
    );
    let headers = client.build_headers().unwrap();
    assert_eq!(
        headers.get("anthropic-beta").unwrap(),
        "claude-code-20250219,oauth-2025-04-20"
    );
    assert_eq!(headers.get("x-app").unwrap(), "cli");
    assert_eq!(headers.get("user-agent").unwrap(), "claude-cli/2.1.75");
    assert_eq!(
        headers
            .get("anthropic-dangerous-direct-browser-access")
            .unwrap(),
        "true"
    );
    assert_eq!(
        headers.get("authorization").unwrap().to_str().unwrap(),
        "Bearer oauth-token"
    );
    assert!(headers.get("x-api-key").is_none());

    let body = encode_anthropic_body_with_options(
        &LlmRequest::builder("claude")
            .system_prompt("repo prompt")
            .message(Message::from_user_text("hi"))
            .build(),
        true,
    );
    assert_eq!(
        body["system"],
        json!([
            {
                "type": "text",
                "text": "You are Claude Code, Anthropic's official CLI for Claude."
            },
            {
                "type": "text",
                "text": "repo prompt"
            }
        ])
    );
}

// AC-llm-client-05: a forced SSE parse failure logs without echoing frame
// content (no secrets/system_prompt/tool input in the log fields).
#[tokio::test]
#[traced_test]
async fn parse_error_log_omits_secrets() {
    let results =
        decode_fixture(include_str!("../../tests/fixtures/anthropic/malformed.sse")).await;
    // The stream ends with exactly one Decode error item that preserves the
    // captured request-id (§8.8).
    let last = results.last().expect("at least one item");
    match last {
        Err(e) => {
            assert_eq!(e.kind, crate::error::ProviderErrorKind::Decode);
            assert_eq!(e.request_id.as_deref(), Some("req-test"));
        }
        Ok(event) => panic!("expected a decode error, got {event:?}"),
    }

    assert!(logs_contain("anthropic sse frame failed to parse"));
    assert!(!logs_contain("SUPERSECRET"), "log leaked frame content");
}
