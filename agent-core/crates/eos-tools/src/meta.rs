//! The canonical per-tool registration metadata: intent, terminal flag, and the
//! pre-hook chain.
//!
//! This is the single source of the static `@tool(intent=…, is_terminal_tool=…,
//! pre_hooks=…)` facts (the registration table in
//! `_framework/factory.py` + each tool's decorator). It is `pub(crate)` table
//! data — deliberately not methods on [`ToolName`], keeping that type a pure
//! name (intent/terminal live on `RegisteredTool`, not the name).

use crate::hooks::Hook;
use crate::intent::ToolIntent;
use crate::name::ToolName;

/// The classification intent for a tool (GC-tools-05: every tool, including the
/// synthesized subagent controls, carries an explicit intent).
#[must_use]
pub(crate) fn tool_intent(name: ToolName) -> ToolIntent {
    use ToolName as T;
    match name {
        T::WriteFile
        | T::EditFile
        | T::MultiEdit
        | T::ExecCommand
        | T::WriteStdin
        | T::RunSubagent => ToolIntent::WriteAllowed,
        T::DelegateWorkflow
        | T::CancelWorkflow
        | T::EnterIsolatedWorkspace
        | T::ExitIsolatedWorkspace => ToolIntent::Lifecycle,
        T::ReadFile
        | T::Grep
        | T::Glob
        | T::CheckWorkflowStatus
        | T::AskAdvisor
        | T::LoadSkillReference
        | T::CheckSubagentProgress
        | T::CancelSubagent
        | T::SubmitRootOutcome
        | T::SubmitGeneratorOutcome
        | T::SubmitReducerOutcome
        | T::SubmitPlannerOutcome
        | T::SubmitAdvisorFeedback
        | T::SubmitExplorationResult => ToolIntent::ReadOnly,
    }
}

/// Whether a successful call ends the agent run (the six `submit_*` terminals).
#[must_use]
pub(crate) fn is_terminal(name: ToolName) -> bool {
    crate::terminal::TerminalTool::from_tool_name(name).is_some()
}

/// The ordered pre-hook chain for a tool (empty for tools with no hooks).
///
/// Order is load-bearing: `RequireNoInflightBackgroundTasks` is wired **before**
/// `AdvisorApproval` so background rejection surfaces first.
#[must_use]
pub(crate) fn tool_hooks(name: ToolName) -> Vec<Hook> {
    use ToolName as T;
    match name {
        T::ExecCommand => vec![
            Hook::DestructiveGitShell { tool: name },
            Hook::DestructiveShell { tool: name },
        ],
        T::EnterIsolatedWorkspace | T::ExitIsolatedWorkspace => {
            vec![Hook::RequireNoInflightBackgroundTasks { tool: name }]
        }
        // EOS decision: the root terminal is advisor-gated too. This diverges
        // from the Python backend, which gates only the planner/generator/reducer
        // main-role terminals and intentionally omits root. RequireNoInflight is
        // kept first so a background rejection surfaces before the advisor gate.
        T::SubmitRootOutcome => vec![
            Hook::RequireNoInflightBackgroundTasks { tool: name },
            Hook::AdvisorApproval { tool: name },
        ],
        T::SubmitGeneratorOutcome | T::SubmitReducerOutcome => vec![
            Hook::RequireNoInflightBackgroundTasks { tool: name },
            Hook::AdvisorApproval { tool: name },
        ],
        T::SubmitPlannerOutcome => vec![
            Hook::RequireNoInflightBackgroundTasks { tool: name },
            Hook::DisallowNestedPlannerDeferral { tool: name },
            Hook::AdvisorApproval { tool: name },
        ],
        T::AskAdvisor => vec![Hook::BlockInIsolatedMode { tool: name }],
        _ => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn advisor_hook_count(name: ToolName) -> usize {
        tool_hooks(name)
            .iter()
            .filter(|hook| matches!(hook, Hook::AdvisorApproval { .. }))
            .count()
    }

    /// Port of `test_advisor_gate_wiring.py`: exactly the four `submit_*_outcome`
    /// terminals carry one `AdvisorApproval` hook (targeting themselves); the
    /// helper/explorer terminals and `ask_advisor` carry none. Rust gates
    /// `submit_root_outcome` too — a deliberate divergence from Python (which omits
    /// root); this assertion reflects that intended difference.
    #[test]
    fn advisor_gate_wired_on_exactly_the_four_main_terminals() {
        for gated in [
            ToolName::SubmitRootOutcome,
            ToolName::SubmitPlannerOutcome,
            ToolName::SubmitGeneratorOutcome,
            ToolName::SubmitReducerOutcome,
        ] {
            assert_eq!(
                advisor_hook_count(gated),
                1,
                "{gated:?} must carry exactly one AdvisorApproval hook"
            );
            assert!(
                tool_hooks(gated).iter().any(|hook| matches!(
                    hook,
                    Hook::AdvisorApproval { tool } if *tool == gated
                )),
                "{gated:?}'s AdvisorApproval hook must target itself"
            );
        }
        for ungated in [
            ToolName::AskAdvisor,
            ToolName::SubmitAdvisorFeedback,
            ToolName::SubmitExplorationResult,
        ] {
            assert_eq!(
                advisor_hook_count(ungated),
                0,
                "{ungated:?} must NOT be advisor-gated (else ask_advisor self-gates / deadlocks)"
            );
        }
    }

    /// `RequireNoInflightBackgroundTasks` precedes `AdvisorApproval` on every gated
    /// terminal so a background rejection surfaces before the advisor gate
    /// (load-bearing ordering, advisor remediation plan §3).
    #[test]
    fn no_inflight_precedes_advisor_on_gated_terminals() {
        for gated in [
            ToolName::SubmitRootOutcome,
            ToolName::SubmitPlannerOutcome,
            ToolName::SubmitGeneratorOutcome,
            ToolName::SubmitReducerOutcome,
        ] {
            let hooks = tool_hooks(gated);
            let no_inflight = hooks
                .iter()
                .position(|hook| matches!(hook, Hook::RequireNoInflightBackgroundTasks { .. }));
            let advisor = hooks
                .iter()
                .position(|hook| matches!(hook, Hook::AdvisorApproval { .. }));
            assert!(
                matches!((no_inflight, advisor), (Some(n), Some(a)) if n < a),
                "{gated:?}: RequireNoInflight must precede AdvisorApproval"
            );
        }
    }
}
