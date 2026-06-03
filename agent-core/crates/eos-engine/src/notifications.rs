//! Declarative notification rules and the notification sink port.

use std::collections::VecDeque;
use std::sync::Arc;

use async_trait::async_trait;
use eos_llm_client::{ContentBlock, Message, MessageRole};
use eos_tools::ports::{
    AdvisorApproval, AdvisorPort, NotificationSink, Sealed, SystemNotification as ToolNotification,
};
use eos_tools::ToolError;
use tokio::sync::Mutex;

use crate::query::QueryContext;

/// A stream- and transcript-visible system notification.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SystemNotification {
    /// Notification text.
    pub text: String,
    /// Agent label.
    pub agent_name: String,
    /// Agent run id as a string.
    pub agent_run_id: String,
}

/// Closed set of engine-owned notification rules.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NotificationRule {
    /// Nudge the model to submit a terminal tool.
    TerminalCallReminder,
    /// Tool-call budget threshold.
    ToolCallBudget {
        /// Human label, such as `75%`.
        label: &'static str,
        /// Threshold numerator.
        numerator: u32,
        /// Threshold denominator.
        denominator: u32,
    },
}

impl NotificationRule {
    /// Stable deduplication key.
    #[must_use]
    pub fn name(&self) -> String {
        match self {
            Self::TerminalCallReminder => "terminal_call_reminder".to_owned(),
            Self::ToolCallBudget { label, .. } => {
                format!("tool_call_budget_{}_percent", label.trim_end_matches('%'))
            }
        }
    }

    /// Whether this rule fires only once per run.
    #[must_use]
    pub const fn fire_once(&self) -> bool {
        matches!(self, Self::ToolCallBudget { .. })
    }

    /// Whether this rule should fire for the current top-of-turn state.
    #[must_use]
    pub fn trigger(&self, messages: &[Message], ctx: &QueryContext) -> bool {
        if ctx.terminal_result.as_ref().is_some_and(|r| r.is_terminal) {
            return false;
        }
        match self {
            // The nudge fires only when the most recent assistant turn was a
            // *text return* — a `Text` block and no `ToolUse` (anchor §6.2). A
            // reasoning-only turn has no `Text` block, so it never nudges; a
            // tool-use turn is making progress, so it never nudges either.
            Self::TerminalCallReminder => {
                !ctx.terminal_tools.is_empty() && last_assistant_was_text_return(messages)
            }
            Self::ToolCallBudget {
                numerator,
                denominator,
                ..
            } => {
                if ctx.tool_call_limit == 0 || *denominator == 0 {
                    return false;
                }
                ctx.tool_calls_used.saturating_mul(*denominator)
                    >= ctx.tool_call_limit.saturating_mul(*numerator)
            }
        }
    }

    /// Render the reminder text.
    #[must_use]
    pub fn body(&self, ctx: &QueryContext) -> String {
        // `ceiling`/`turns_remaining` mirror the Python rule bodies: the run
        // fails at `ceil(1.5 * tool_call_limit)` tool calls, and the displayed
        // `turns_remaining` is derived from `tool_calls_used` alone (the
        // hard-ceiling gate itself uses the call+text-turn sum — §8.4).
        let used = ctx.tool_calls_used;
        let limit = ctx.tool_call_limit;
        let ceiling = limit.saturating_mul(3).div_ceil(2);
        let turns_remaining = ceiling.saturating_sub(used);
        match self {
            Self::TerminalCallReminder => {
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
            Self::ToolCallBudget { label, .. } => {
                format!(
                    "Tool-call budget warning: {label} of the planned budget has been \
                     used ({used}/{limit} tool calls). Submit a terminal tool as soon \
                     as the work is complete; the run will fail at {ceiling} tool calls \
                     ({turns_remaining} remaining)."
                )
            }
        }
    }
}

/// Default notification rules, deduped by name.
#[must_use]
pub fn make_default_notification_rules() -> Vec<NotificationRule> {
    let rules = [
        NotificationRule::ToolCallBudget {
            label: "75%",
            numerator: 3,
            denominator: 4,
        },
        NotificationRule::ToolCallBudget {
            label: "100%",
            numerator: 1,
            denominator: 1,
        },
        NotificationRule::ToolCallBudget {
            label: "125%",
            numerator: 5,
            denominator: 4,
        },
        NotificationRule::TerminalCallReminder,
    ];
    let mut seen = std::collections::BTreeSet::new();
    rules
        .into_iter()
        .filter(|rule| seen.insert(rule.name()))
        .collect()
}

/// Whether the most recent assistant turn was a *text return*: it carries a
/// [`ContentBlock::Text`] and no [`ContentBlock::ToolUse`] (anchor §6.2). A
/// reasoning-only turn (no `Text`) returns `false`.
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

/// Evaluate notification rules in list order and enqueue the firing ones onto
/// the sink (anchor D4: every notification is a sink producer). Fire-once budget
/// tiers are latched in `ctx.notification_fired`; the loop drains the sink.
pub async fn enqueue_notification_rules(
    messages: &[Message],
    ctx: &mut QueryContext,
    sink: &dyn NotificationSink,
) {
    for rule in ctx.notification_rules.clone() {
        let name = rule.name();
        if rule.fire_once() && ctx.notification_fired.contains(&name) {
            continue;
        }
        if rule.trigger(messages, ctx) {
            if rule.fire_once() {
                ctx.notification_fired.insert(name.clone());
            }
            let _ = sink
                .notify_system(ToolNotification {
                    event: name,
                    message: rule.body(ctx),
                })
                .await;
        }
    }
}

/// Queue-backed notification sink for tools and hooks.
#[derive(Debug, Default, Clone)]
pub struct NotificationService {
    queue: Arc<Mutex<VecDeque<ToolNotification>>>,
}

impl NotificationService {
    /// Create an empty service.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Drain queued notifications.
    pub async fn drain(&self) -> Vec<ToolNotification> {
        self.queue.lock().await.drain(..).collect()
    }
}

impl Sealed for NotificationService {}

#[async_trait]
impl NotificationSink for NotificationService {
    async fn notify_system(&self, notification: ToolNotification) -> Result<(), ToolError> {
        self.queue.lock().await.push_back(notification);
        Ok(())
    }
}

/// Minimal advisor port implementation used until `eos-runtime` wires a helper
/// runner around the engine loop.
#[derive(Debug, Default, Clone)]
pub struct AdvisorService;

impl Sealed for AdvisorService {}

#[async_trait]
impl AdvisorPort for AdvisorService {
    async fn review(
        &self,
        tool_name: &str,
        _tool_payload: &eos_types::JsonObject,
    ) -> Result<String, ToolError> {
        Ok(format!(
            "Advisor runner is not wired for `{tool_name}` in this engine-only phase."
        ))
    }

    async fn approval_status(&self, _target_tool: &str) -> Result<AdvisorApproval, ToolError> {
        Ok(AdvisorApproval {
            approved: false,
            reason: Some("missing".to_owned()),
        })
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used)]

    use std::collections::BTreeSet;
    use std::path::PathBuf;
    use std::sync::Arc;

    use eos_tools::{ToolName, ToolRegistry, ToolResult};
    use eos_types::{AgentRunId, JsonObject};

    use super::*;
    use crate::test_support::metadata;

    fn ctx() -> QueryContext {
        QueryContext {
            tool_registry: Arc::new(ToolRegistry::new()),
            cwd: PathBuf::new(),
            model: "m".to_owned(),
            system_prompt: String::new(),
            max_tokens: 1,
            tool_call_limit: 4,
            agent_name: "root".to_owned(),
            agent_run_id: AgentRunId::new_v4(),
            task_id: None,
            tool_calls_used: 0,
            text_only_no_terminal_turns: 0,
            tool_metadata: metadata(),
            enable_background_tasks: true,
            terminal_tools: BTreeSet::from([ToolName::SubmitRootOutcome]),
            exit_reason: None,
            terminal_result: None,
            event_source: None,
            prompt_report: None,
            notification_rules: make_default_notification_rules(),
            notification_fired: BTreeSet::new(),
            notification_state: JsonObject::new(),
            notifier: NotificationService::new(),
        }
    }

    fn assistant_turn() -> [Message; 1] {
        [Message {
            role: MessageRole::Assistant,
            content: vec![ContentBlock::Text {
                text: "done".to_owned(),
            }],
        }]
    }

    async fn fire_rules(messages: &[Message], ctx: &mut QueryContext) -> Vec<ToolNotification> {
        let notifier = ctx.notifier.clone();
        enqueue_notification_rules(messages, ctx, &notifier).await;
        notifier.drain().await
    }

    #[tokio::test]
    async fn notification_rules_fire_in_order_with_dedup() {
        let mut ctx = ctx();
        ctx.tool_calls_used = 3;
        let turn = assistant_turn();
        let first = fire_rules(&turn, &mut ctx).await;
        assert_eq!(first.len(), 2, "75% budget + terminal reminder");
        assert!(first[0].message.contains("75%"));
        assert!(first[1].message.contains("terminal tool"));

        let second = fire_rules(&turn, &mut ctx).await;
        assert_eq!(second.len(), 1, "budget tier is fire-once");
        assert!(second[0].message.contains("terminal tool"));

        ctx.terminal_result = Some(ToolResult {
            output: "done".to_owned(),
            is_error: false,
            metadata: JsonObject::new(),
            is_terminal: true,
        });
        assert!(fire_rules(&turn, &mut ctx).await.is_empty());
    }

    #[tokio::test]
    async fn terminal_reminder_needs_assistant_turn_and_reports_budget() {
        // Parity with `must_submit_terminal_tool.py` + the ported body text.
        let mut ctx = ctx(); // tool_call_limit = 4 -> 75% threshold = 3
        ctx.tool_calls_used = 2; // below the first budget tier; only the reminder can fire

        // Turn 1: user-only transcript -> no terminal reminder (matches Python).
        assert!(fire_rules(&[Message::from_user_text("hi")], &mut ctx)
            .await
            .is_empty());

        // After the model speaks, the reminder fires with the ceil(1.5*limit) ceiling.
        let fired = fire_rules(&assistant_turn(), &mut ctx).await;
        assert_eq!(fired.len(), 1);
        let body = &fired[0].message;
        assert!(body.contains("You have not submitted a terminal tool"));
        assert!(body.contains("2/4 tool calls used"));
        assert!(body.contains("the run will fail at 6 tool calls (4 remaining)"));
    }

    #[tokio::test]
    async fn notification_service_queues_and_drains() {
        let service = NotificationService::new();
        service
            .notify_system(ToolNotification {
                event: "evt".to_owned(),
                message: "body".to_owned(),
            })
            .await
            .expect("notify");
        let drained = service.drain().await;
        assert_eq!(drained.len(), 1);
        assert!(service.drain().await.is_empty());
    }
}
