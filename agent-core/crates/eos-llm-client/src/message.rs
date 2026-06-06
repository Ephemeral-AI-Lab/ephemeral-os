//! The provider-neutral conversation vocabulary: [`Message`], [`MessageRole`],
//! and the [`ContentBlock`] discriminated union.
//!
//! This is the neutral transcript/audit representation, not the provider wire
//! format. Provider-specific encode/decode lives in the provider client files.

use eos_types::{JsonObject, ToolUseId};
use serde::{Deserialize, Serialize};

/// The role of a conversation message.
///
/// There is deliberately no `System` variant: the system prompt is the
/// `LlmRequest.system_prompt` request field, never a message. Deserializing
/// `"system"` therefore fails.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageRole {
    /// A user (or engine-on-behalf-of-user) message.
    User,
    /// A model-authored message.
    Assistant,
}

impl MessageRole {
    /// The wire string the provider encoders emit (`"user"` / `"assistant"`).
    pub(crate) fn as_wire(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Assistant => "assistant",
        }
    }
}

/// A single content block within a [`Message`].
///
/// The `#[serde(alias = "thinking")]` compatibility alias keeps older JSONL
/// transcripts decodable while serialization always emits the `"reasoning"` tag.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
#[non_exhaustive]
pub enum ContentBlock {
    /// Plain text content.
    Text {
        /// The text body.
        text: String,
    },
    /// A model request to execute a named tool.
    ToolUse {
        /// The provider-assigned tool-use id.
        tool_use_id: ToolUseId,
        /// The tool name.
        name: String,
        /// The tool arguments.
        input: JsonObject,
    },
    /// Model reasoning content.
    #[serde(alias = "thinking")]
    Reasoning {
        /// The reasoning text.
        text: String,
    },
    /// A tool result sent back to the model.
    ToolResult {
        /// The tool-use id this result answers.
        tool_use_id: ToolUseId,
        /// The result body.
        content: String,
        /// Whether the result is an error.
        #[serde(default)]
        is_error: bool,
        /// Engine-side metadata (persisted in transcripts/audit; omitted from
        /// the provider wire body by the encoders).
        #[serde(default)]
        metadata: JsonObject,
        /// Engine marker set when a successful terminal tool returned (consumed
        /// by the query loop; omitted from the provider wire body).
        #[serde(default)]
        is_terminal: bool,
    },
    /// An engine-generated reminder. Anthropic encode flattens it to a `text`
    /// block wrapped in `<system-reminder>…</system-reminder>`.
    SystemNotification {
        /// The reminder text.
        text: String,
    },
}

/// A single assistant or user message.
///
/// Source: `message.py::Message`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Message {
    /// The message role.
    pub role: MessageRole,
    /// The ordered content blocks.
    #[serde(default)]
    pub content: Vec<ContentBlock>,
}

impl Message {
    /// Construct a user message from raw text (`Message.from_user_text`).
    #[must_use]
    pub fn from_user_text(text: impl Into<String>) -> Self {
        Self {
            role: MessageRole::User,
            content: vec![ContentBlock::Text { text: text.into() }],
        }
    }

    /// Concatenated text blocks (excludes reasoning and notifications).
    #[must_use]
    pub fn assistant_text(&self) -> String {
        self.content
            .iter()
            .filter_map(|b| match b {
                ContentBlock::Text { text } => Some(text.as_str()),
                _ => None,
            })
            .collect()
    }

    /// Concatenated reasoning blocks (`Message.thinking`).
    #[must_use]
    pub fn reasoning_text(&self) -> String {
        self.content
            .iter()
            .filter_map(|b| match b {
                ContentBlock::Reasoning { text } => Some(text.as_str()),
                _ => None,
            })
            .collect()
    }

    /// Iterate the tool-use blocks in the message.
    pub fn tool_uses(&self) -> impl Iterator<Item = &ContentBlock> {
        self.content
            .iter()
            .filter(|b| matches!(b, ContentBlock::ToolUse { .. }))
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use serde_json::json;

    // AC-llm-client-03: a legacy "thinking" block decodes to Reasoning; encode
    // always emits "reasoning".
    #[test]
    fn reasoning_compat_decode_maps_thinking() {
        let legacy: ContentBlock =
            serde_json::from_value(json!({"type": "thinking", "text": "hmm"})).unwrap();
        assert_eq!(legacy, ContentBlock::Reasoning { text: "hmm".into() });

        // Round-trip serialization emits the new tag, never "thinking".
        let encoded = serde_json::to_value(&legacy).unwrap();
        assert_eq!(encoded, json!({"type": "reasoning", "text": "hmm"}));
        assert_ne!(encoded["type"], json!("thinking"));

        // The new tag also decodes.
        let modern: ContentBlock =
            serde_json::from_value(json!({"type": "reasoning", "text": "hmm"})).unwrap();
        assert_eq!(modern, legacy);
    }

    // AC-llm-client-07: MessageRole rejects "system"; system text only via
    // LlmRequest.system_prompt (no Message can hold it).
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
        // is_terminal/metadata default on decode and are present on the neutral
        // (transcript) serialization.
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
        let tuid: ToolUseId = "toolu_9".parse().unwrap();
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
                    tool_use_id: tuid,
                    name: "read_file".into(),
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
}
