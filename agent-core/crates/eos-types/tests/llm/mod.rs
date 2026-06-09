#![allow(clippy::unwrap_used)]

use super::*;
use serde_json::json;

#[test]
fn reasoning_compat_decode_maps_thinking() {
    let legacy: ContentBlock =
        serde_json::from_value(json!({"type": "thinking", "text": "hmm"})).unwrap();
    assert_eq!(legacy, ContentBlock::Reasoning { text: "hmm".into() });

    let encoded = serde_json::to_value(&legacy).unwrap();
    assert_eq!(encoded, json!({"type": "reasoning", "text": "hmm"}));
    assert_ne!(encoded["type"], json!("thinking"));

    let modern: ContentBlock =
        serde_json::from_value(json!({"type": "reasoning", "text": "hmm"})).unwrap();
    assert_eq!(modern, legacy);
}

#[test]
fn message_role_has_no_system() {
    let user: MessageRole = serde_json::from_value(json!("user")).unwrap();
    assert_eq!(user, MessageRole::User);
    let assistant: MessageRole = serde_json::from_value(json!("assistant")).unwrap();
    assert_eq!(assistant, MessageRole::Assistant);

    let err = serde_json::from_value::<MessageRole>(json!("system"));
    assert!(err.is_err(), "system role must be rejected");
}

#[test]
fn tool_result_defaults_and_wire_irrelevant_fields_round_trip() {
    let block: ContentBlock = serde_json::from_value(json!({
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "ok",
    }))
    .unwrap();
    match &block {
        ContentBlock::ToolResult {
            is_error,
            is_terminal,
            metadata,
            ..
        } => {
            assert!(!is_error);
            assert!(!is_terminal);
            assert!(metadata.is_empty());
        }
        other => panic!("expected tool_result, got {other:?}"),
    }
    let encoded = serde_json::to_value(&block).unwrap();
    assert_eq!(encoded["is_terminal"], json!(false));
    assert!(encoded.get("metadata").is_some());
}

#[test]
fn message_helpers() {
    let tool_use_id: ToolUseId = "toolu_9".parse().unwrap();
    let msg = Message {
        role: MessageRole::Assistant,
        content: vec![
            ContentBlock::Reasoning {
                text: "think".into(),
            },
            ContentBlock::Text {
                text: "Hello".into(),
            },
            ContentBlock::Text {
                text: " world".into(),
            },
            ContentBlock::ToolUse {
                tool_use_id,
                name: "run".into(),
                input: JsonObject::new(),
            },
        ],
    };
    assert_eq!(msg.assistant_text(), "Hello world");
    assert_eq!(msg.reasoning_text(), "think");
    assert_eq!(msg.tool_uses().count(), 1);

    let user = Message::from_user_text("hi");
    assert_eq!(user.role, MessageRole::User);
    assert_eq!(user.assistant_text(), "hi");
}
