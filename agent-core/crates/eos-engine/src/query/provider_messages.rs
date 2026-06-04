//! Provider-facing message preparation.

use std::collections::BTreeSet;

use eos_llm_client::{ContentBlock, Message, MessageRole};
use eos_types::ToolUseId;

pub(crate) fn build_provider_messages(messages: &[Message]) -> Vec<Message> {
    let mut sanitized = messages.to_vec();
    drop_unmatched_tool_blocks(&mut sanitized);
    sanitized
        .into_iter()
        .filter(|message| !message.content.is_empty())
        .collect()
}

fn message_tool_use_ids(message: &Message) -> BTreeSet<ToolUseId> {
    message
        .content
        .iter()
        .filter_map(|block| match block {
            ContentBlock::ToolUse { tool_use_id, .. } => Some(tool_use_id.clone()),
            _ => None,
        })
        .collect()
}

fn message_tool_result_ids(message: &Message) -> BTreeSet<ToolUseId> {
    message
        .content
        .iter()
        .filter_map(|block| match block {
            ContentBlock::ToolResult { tool_use_id, .. } => Some(tool_use_id.clone()),
            _ => None,
        })
        .collect()
}

fn remove_tool_uses(message: &mut Message, tool_use_ids: &BTreeSet<ToolUseId>) {
    if tool_use_ids.is_empty() {
        return;
    }
    message.content.retain(|block| match block {
        ContentBlock::ToolUse { tool_use_id, .. } => !tool_use_ids.contains(tool_use_id),
        _ => true,
    });
}

fn remove_tool_results(message: &mut Message, tool_result_ids: &BTreeSet<ToolUseId>) {
    if tool_result_ids.is_empty() {
        return;
    }
    message.content.retain(|block| match block {
        ContentBlock::ToolResult { tool_use_id, .. } => !tool_result_ids.contains(tool_use_id),
        _ => true,
    });
}

fn drop_unmatched_tool_blocks(messages: &mut [Message]) {
    let mut pending_tool_use_ids = BTreeSet::new();
    let mut pending_message_index = None;

    for message_index in 0..messages.len() {
        let tool_use_ids = message_tool_use_ids(&messages[message_index]);
        let mut tool_result_ids = message_tool_result_ids(&messages[message_index]);
        let mut matched_pending_tool_uses = false;

        if !pending_tool_use_ids.is_empty() {
            let current_message_matches = messages[message_index].role == MessageRole::User
                && pending_tool_use_ids.is_subset(&tool_result_ids);

            if current_message_matches {
                let unmatched_result_ids = tool_result_ids
                    .difference(&pending_tool_use_ids)
                    .cloned()
                    .collect();
                remove_tool_results(&mut messages[message_index], &unmatched_result_ids);
                pending_tool_use_ids.clear();
                pending_message_index = None;
                matched_pending_tool_uses = true;
            } else {
                if let Some(index) = pending_message_index {
                    remove_tool_uses(&mut messages[index], &pending_tool_use_ids);
                }
                pending_tool_use_ids.clear();
                pending_message_index = None;
                tool_result_ids = message_tool_result_ids(&messages[message_index]);
            }
        }

        if !tool_result_ids.is_empty() && tool_use_ids.is_empty() && !matched_pending_tool_uses {
            messages[message_index]
                .content
                .retain(|block| !matches!(block, ContentBlock::ToolResult { .. }));
        }

        let tool_use_ids = message_tool_use_ids(&messages[message_index]);
        if !tool_use_ids.is_empty() {
            pending_tool_use_ids = tool_use_ids;
            pending_message_index = Some(message_index);
        }
    }

    if !pending_tool_use_ids.is_empty() {
        if let Some(index) = pending_message_index {
            remove_tool_uses(&mut messages[index], &pending_tool_use_ids);
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used)]

    use eos_types::JsonObject;

    use super::*;

    fn id(value: &str) -> ToolUseId {
        value.parse().expect("tool use id")
    }

    fn assistant_tool_use(value: &str) -> Message {
        Message {
            role: MessageRole::Assistant,
            content: vec![ContentBlock::ToolUse {
                tool_use_id: id(value),
                name: "read_file".to_owned(),
                input: JsonObject::new(),
            }],
        }
    }

    fn user_tool_results(values: &[&str]) -> Message {
        Message {
            role: MessageRole::User,
            content: values
                .iter()
                .map(|value| ContentBlock::ToolResult {
                    tool_use_id: id(value),
                    content: format!("result {value}"),
                    is_error: false,
                    metadata: JsonObject::new(),
                    is_terminal: false,
                })
                .collect(),
        }
    }

    #[test]
    fn keeps_matched_tool_pair() {
        let messages = vec![
            assistant_tool_use("toolu_a"),
            user_tool_results(&["toolu_a"]),
        ];

        let sanitized = build_provider_messages(&messages);

        assert_eq!(sanitized, messages);
    }

    #[test]
    fn drops_trailing_tool_use_from_provider_copy_only() {
        let messages = vec![assistant_tool_use("toolu_a")];

        let sanitized = build_provider_messages(&messages);

        assert!(sanitized.is_empty());
        assert_eq!(messages[0].content.len(), 1);
    }

    #[test]
    fn drops_orphan_tool_result() {
        let messages = vec![user_tool_results(&["toolu_a"])];

        let sanitized = build_provider_messages(&messages);

        assert!(sanitized.is_empty());
    }

    #[test]
    fn drops_extra_tool_result_from_matched_result_message() {
        let messages = vec![
            assistant_tool_use("toolu_a"),
            user_tool_results(&["toolu_a", "toolu_extra"]),
        ];

        let sanitized = build_provider_messages(&messages);

        assert_eq!(sanitized.len(), 2);
        assert_eq!(
            message_tool_result_ids(&sanitized[1]),
            BTreeSet::from([id("toolu_a")])
        );
    }

    #[test]
    fn drops_partial_mismatch_on_next_message() {
        let messages = vec![
            assistant_tool_use("toolu_a"),
            user_tool_results(&["toolu_b"]),
        ];

        let sanitized = build_provider_messages(&messages);

        assert!(sanitized.is_empty());
    }
}
