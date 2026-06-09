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
#[path = "../tests/llm/mod.rs"]
mod tests;
