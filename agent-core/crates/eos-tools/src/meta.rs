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
        T::SubmitRootOutcome => vec![Hook::RequireNoInflightBackgroundTasks { tool: name }],
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
