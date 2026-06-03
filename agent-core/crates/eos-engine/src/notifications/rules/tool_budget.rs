//! The tool-call budget reminder rule (anchor §6.1).
//!
//! Fires once per tier (75/100/125% of the planned `tool_call_limit`) to warn
//! the model that it is approaching the hard ceiling at which the run fails.

use eos_llm_client::Message;

use crate::notifications::{budget_figures, NotificationRule};
use crate::query::QueryContext;

/// A single tool-call budget tier (e.g. `75%`), firing once when
/// `tool_calls_used * denominator >= tool_call_limit * numerator`.
#[derive(Debug, Clone)]
pub struct ToolCallBudget {
    /// Human label, such as `75%`.
    label: &'static str,
    /// Threshold numerator.
    numerator: u32,
    /// Threshold denominator.
    denominator: u32,
}

impl ToolCallBudget {
    /// Construct one budget tier.
    #[must_use]
    pub const fn new(label: &'static str, numerator: u32, denominator: u32) -> Self {
        Self {
            label,
            numerator,
            denominator,
        }
    }
}

impl NotificationRule for ToolCallBudget {
    fn name(&self) -> String {
        format!("tool_call_budget_{}_percent", self.label.trim_end_matches('%'))
    }

    fn fire_once(&self) -> bool {
        true
    }

    fn trigger(&self, _messages: &[Message], ctx: &QueryContext) -> bool {
        if ctx.tool_call_limit == 0 || self.denominator == 0 {
            return false;
        }
        ctx.tool_calls_used.saturating_mul(self.denominator)
            >= ctx.tool_call_limit.saturating_mul(self.numerator)
    }

    fn body(&self, ctx: &QueryContext) -> String {
        let (used, limit, ceiling, turns_remaining) = budget_figures(ctx);
        let label = self.label;
        format!(
            "Tool-call budget warning: {label} of the planned budget has been \
             used ({used}/{limit} tool calls). Submit a terminal tool as soon \
             as the work is complete; the run will fail at {ceiling} tool calls \
             ({turns_remaining} remaining)."
        )
    }
}
