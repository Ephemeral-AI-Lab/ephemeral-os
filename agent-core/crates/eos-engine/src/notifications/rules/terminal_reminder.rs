//! The terminal-submit reminder rule (anchor §6.2).
//!
//! Nudges the model to call a terminal tool when its most recent assistant turn
//! was a *text return* — a `Text` block and no `ToolUse`. A reasoning-only turn
//! (no `Text` block) does not nudge; a tool-use turn is making progress, so it
//! does not nudge either.

use eos_llm_client::{ContentBlock, Message, MessageRole};

use crate::notifications::{budget_figures, NotificationRule, NotificationRuleContext};

/// Reminds the model to submit a terminal tool after a bare text return.
#[derive(Debug, Clone, Copy, Default)]
pub struct TerminalCallReminder;

impl NotificationRule for TerminalCallReminder {
    fn name(&self) -> String {
        "terminal_call_reminder".to_owned()
    }

    fn fire_once(&self) -> bool {
        false
    }

    fn trigger(&self, messages: &[Message], ctx: &NotificationRuleContext<'_>) -> bool {
        !ctx.terminal_tools.is_empty() && last_assistant_was_text_return(messages)
    }

    fn body(&self, ctx: &NotificationRuleContext<'_>) -> String {
        let (used, limit, ceiling, turns_remaining) = budget_figures(ctx);
        let mut names: Vec<&str> = ctx
            .terminal_tools
            .iter()
            .map(|name| name.as_str())
            .collect();
        names.sort_unstable();
        let names = names.join(", ");
        format!(
            "You have not submitted a terminal tool. Deliver your result by \
             calling one of: {names}. Budget: {used}/{limit} tool calls used; \
             the run will fail at {ceiling} tool calls ({turns_remaining} remaining)."
        )
    }
}

/// Whether the most recent assistant turn was a text return: a
/// [`ContentBlock::Text`] and no [`ContentBlock::ToolUse`]. A reasoning-only
/// turn (no `Text`) returns `false`.
fn last_assistant_was_text_return(messages: &[Message]) -> bool {
    let Some(message) = messages
        .iter()
        .rev()
        .find(|message| message.role == MessageRole::Assistant)
    else {
        return false;
    };
    let has_text = message
        .content
        .iter()
        .any(|block| matches!(block, ContentBlock::Text { .. }));
    let has_tool_use = message
        .content
        .iter()
        .any(|block| matches!(block, ContentBlock::ToolUse { .. }));
    has_text && !has_tool_use
}
