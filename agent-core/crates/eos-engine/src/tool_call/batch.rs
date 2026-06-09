//! Pure batch-dispatch decision functions: terminal-batch rejection and
//! lifecycle-batch policy.
//!
//! These are pure, synchronous functions over a batch of calls and registry
//! lookups: no async, no shared state. The async loop that consumes their
//! decisions lives in `agent_loop::executor`; predicate tests live here.

use eos_tool::{ToolIntent, ToolRegistry};

/// One call in a model-emitted tool-use batch. The `name` is the raw wire string
/// (possibly unknown to the registry).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DispatchCall<'a> {
    /// The provider tool-use id.
    pub tool_use_id: &'a str,
    /// The raw tool name the model emitted.
    pub name: &'a str,
}

/// A rejection the engine renders back as an errored tool result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BatchRejection {
    /// The rejected call's tool-use id.
    pub tool_use_id: String,
    /// The model-facing rejection message.
    pub message: String,
}

/// The lifecycle-batch decision: which calls are rejected, which dispatch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LifecycleBatchDecision {
    /// Calls rejected before dispatch.
    pub rejected: Vec<BatchRejection>,
    /// Tool-use ids the engine should dispatch.
    pub dispatched: Vec<String>,
}

fn is_terminal(name: &str, registry: &ToolRegistry) -> bool {
    registry.get_wire(name).is_some_and(|tool| tool.is_terminal)
}

fn intent_of(name: &str, registry: &ToolRegistry) -> Option<ToolIntent> {
    registry.get_wire(name).map(|tool| tool.intent)
}

fn backticked(name: &str) -> String {
    format!("`{name}`")
}

/// Terminal-batch rejection (§8.4): when the batch has more than one call and any
/// call is a terminal tool, reject **every** call. A solo terminal call is
/// allowed (returns `None`).
#[must_use]
pub fn reject_terminal_batch(
    calls: &[DispatchCall],
    registry: &ToolRegistry,
) -> Option<Vec<BatchRejection>> {
    if calls.len() <= 1 {
        return None;
    }
    let terminal: Vec<&DispatchCall> = calls
        .iter()
        .filter(|call| is_terminal(call.name, registry))
        .collect();
    if terminal.is_empty() {
        return None;
    }

    let mut flagged: Vec<String> = terminal.iter().map(|call| backticked(call.name)).collect();
    flagged.sort();
    flagged.dedup();
    let flagged_names = flagged.join(", ");
    let called_names = calls
        .iter()
        .map(|call| backticked(call.name))
        .collect::<Vec<_>>()
        .join(", ");

    let message = format!(
        "Terminal tool {flagged_names} must be called alone. This response batched it with other \
         tools: {called_names}. No tool in this batch executed. Resubmit with only the exclusive \
         tool in its own final batch."
    );

    Some(
        calls
            .iter()
            .map(|call| BatchRejection {
                tool_use_id: call.tool_use_id.to_owned(),
                message: message.clone(),
            })
            .collect(),
    )
}

/// Lifecycle-batch policy (§8.5): `>1` lifecycle → reject all lifecycle, siblings
/// still dispatch; `=1` lifecycle + ≥1 sibling → reject siblings, the lifecycle
/// call executes solo. No lifecycle → dispatch everything. (Divergence from the
/// terminal precedent is deliberate: forcing the lifecycle call to also retry
/// would loop the agent indefinitely.)
#[must_use]
pub fn lifecycle_batch_decision(
    calls: &[DispatchCall],
    registry: &ToolRegistry,
) -> LifecycleBatchDecision {
    let (lifecycle, non_lifecycle): (Vec<&DispatchCall>, Vec<&DispatchCall>) = calls
        .iter()
        .partition(|call| intent_of(call.name, registry) == Some(ToolIntent::Lifecycle));

    if lifecycle.is_empty() {
        return LifecycleBatchDecision {
            rejected: Vec::new(),
            dispatched: calls.iter().map(|c| c.tool_use_id.to_owned()).collect(),
        };
    }

    if lifecycle.len() > 1 {
        let names = lifecycle
            .iter()
            .map(|c| backticked(c.name))
            .collect::<Vec<_>>()
            .join(", ");
        let message = format!(
            "Multiple lifecycle tools in one batch ({names}); engine cannot choose ordering. \
             Resubmit each lifecycle call in its own batch."
        );
        return LifecycleBatchDecision {
            rejected: lifecycle
                .iter()
                .map(|c| BatchRejection {
                    tool_use_id: c.tool_use_id.to_owned(),
                    message: message.clone(),
                })
                .collect(),
            dispatched: non_lifecycle
                .iter()
                .map(|c| c.tool_use_id.to_owned())
                .collect(),
        };
    }

    // Exactly one lifecycle call.
    let lifecycle_call = lifecycle[0];
    if non_lifecycle.is_empty() {
        return LifecycleBatchDecision {
            rejected: Vec::new(),
            dispatched: vec![lifecycle_call.tool_use_id.to_owned()],
        };
    }

    let sibling_names = non_lifecycle
        .iter()
        .map(|c| backticked(c.name))
        .collect::<Vec<_>>()
        .join(", ");
    let message = format!(
        "`{}` changes workspace routing; sibling tools ({sibling_names}) were rejected to avoid \
         ordering ambiguity. The lifecycle call executed. Resubmit the rejected tools in the next \
         batch.",
        lifecycle_call.name
    );
    LifecycleBatchDecision {
        rejected: non_lifecycle
            .iter()
            .map(|c| BatchRejection {
                tool_use_id: c.tool_use_id.to_owned(),
                message: message.clone(),
            })
            .collect(),
        dispatched: vec![lifecycle_call.tool_use_id.to_owned()],
    }
}

#[cfg(test)]
#[path = "../../tests/tool_call/batch/mod.rs"]
mod tests;
