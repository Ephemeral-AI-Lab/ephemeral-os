//! Provider-neutral model transcript and tool declaration DTOs.
//!
//! Provider clients encode these values into upstream wire formats, but the DTOs
//! themselves are shared by tool, engine, records, and test support.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{JsonObject, ToolUseId};

/// The default completion token cap used when an agent profile does not provide
/// a narrower model request limit.
pub const DEFAULT_MAX_TOKENS: u32 = 32768;

/// The role of a conversation message.
///
/// There is deliberately no `System` variant: the system prompt is the model
/// request's `system_prompt` field, never a message. Deserializing `"system"`
/// therefore fails.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageRole {
    /// A user (or engine-on-behalf-of-user) message.
    User,
    /// A model-authored message.
    Assistant,
}

impl MessageRole {
    /// The canonical token emitted by provider encoders (`"user"` /
    /// `"assistant"`).
    #[must_use]
    pub const fn as_wire(self) -> &'static str {
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
        /// Engine-side metadata persisted in transcripts/audit and omitted from
        /// provider wire bodies by provider encoders.
        #[serde(default)]
        metadata: JsonObject,
        /// Engine marker set when a successful terminal tool returned.
        #[serde(default)]
        is_terminal: bool,
    },
    /// An engine-generated reminder.
    SystemNotification {
        /// The reminder text.
        text: String,
    },
}

/// A single assistant or user message.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Message {
    /// The message role.
    pub role: MessageRole,
    /// The ordered content blocks.
    #[serde(default)]
    pub content: Vec<ContentBlock>,
}

impl Message {
    /// Construct a user message from raw text.
    #[must_use]
    pub fn from_user_text(text: impl Into<String>) -> Self {
        Self {
            role: MessageRole::User,
            content: vec![ContentBlock::Text { text: text.into() }],
        }
    }

    /// Concatenated text blocks, excluding reasoning and notifications.
    #[must_use]
    pub fn assistant_text(&self) -> String {
        self.content
            .iter()
            .filter_map(|block| match block {
                ContentBlock::Text { text } => Some(text.as_str()),
                _ => None,
            })
            .collect()
    }

    /// Concatenated reasoning blocks.
    #[must_use]
    pub fn reasoning_text(&self) -> String {
        self.content
            .iter()
            .filter_map(|block| match block {
                ContentBlock::Reasoning { text } => Some(text.as_str()),
                _ => None,
            })
            .collect()
    }

    /// Iterate the tool-use blocks in the message.
    pub fn tool_uses(&self) -> impl Iterator<Item = &ContentBlock> {
        self.content
            .iter()
            .filter(|block| matches!(block, ContentBlock::ToolUse { .. }))
    }
}

/// A neutral tool declaration sent to the model.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[non_exhaustive]
pub struct ToolSpec {
    /// The tool name the model calls.
    pub name: String,
    /// The model-facing description.
    pub description: String,
    /// The JSON Schema of the tool's input.
    pub input_schema: JsonObject,
    /// The JSON Schema of the tool's output, when authored.
    pub output_schema: Option<JsonObject>,
}

impl ToolSpec {
    /// Construct a tool declaration.
    #[must_use]
    pub fn new(
        name: impl Into<String>,
        description: impl Into<String>,
        input_schema: JsonObject,
        output_schema: Option<JsonObject>,
    ) -> Self {
        Self {
            name: name.into(),
            description: description.into(),
            input_schema,
            output_schema,
        }
    }
}

#[cfg(test)]
mod tests {
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
}
