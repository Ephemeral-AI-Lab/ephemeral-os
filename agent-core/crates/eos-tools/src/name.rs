//! [`ToolName`] — the typed name of every public model-facing tool.
//!
//! Ports `_names.py` **plus** the four names that module omits (`write_stdin`,
//! `enter_isolated_workspace`, `exit_isolated_workspace`, `load_skill_reference`)
//! and the two subagent control tools (`check_subagent_progress`,
//! `cancel_subagent`) — GC-tools-04. The authoritative set is the union of the
//! six registration sites, not `_names.py`. Each variant maps to its wire string
//! (the exact `snake_case` of the variant), so `serde` `rename_all` and the
//! hand-written [`ToolName::as_str`] agree (asserted by a test).

use std::fmt;
use std::str::FromStr;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// The typed name of a public tool (`type-no-stringly`). `#[non_exhaustive]`:
/// new tools are added here, never as raw strings.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum ToolName {
    /// `read_file` (sandbox).
    ReadFile,
    /// `write_file` (sandbox).
    WriteFile,
    /// `edit_file` (sandbox).
    EditFile,
    /// `multi_edit` (sandbox).
    MultiEdit,
    /// `exec_command` (sandbox command session).
    ExecCommand,
    /// `write_stdin` (sandbox command session; omitted from `_names.py`).
    WriteStdin,
    /// `grep` (sandbox).
    Grep,
    /// `glob` (sandbox).
    Glob,
    /// `enter_isolated_workspace` (omitted from `_names.py`).
    EnterIsolatedWorkspace,
    /// `exit_isolated_workspace` (omitted from `_names.py`).
    ExitIsolatedWorkspace,
    /// `run_subagent` (subagent).
    RunSubagent,
    /// `check_subagent_progress` (subagent control).
    CheckSubagentProgress,
    /// `cancel_subagent` (subagent control).
    CancelSubagent,
    /// `ask_advisor` (ask helper).
    AskAdvisor,
    /// `delegate_workflow` (workflow).
    DelegateWorkflow,
    /// `check_workflow_status` (workflow).
    CheckWorkflowStatus,
    /// `cancel_workflow` (workflow).
    CancelWorkflow,
    /// `load_skill_reference` (skills; omitted from `_names.py`).
    LoadSkillReference,
    /// `submit_root_outcome` (submission, terminal).
    SubmitRootOutcome,
    /// `submit_generator_outcome` (submission, terminal).
    SubmitGeneratorOutcome,
    /// `submit_reducer_outcome` (submission, terminal).
    SubmitReducerOutcome,
    /// `submit_planner_outcome` (submission, terminal).
    SubmitPlannerOutcome,
    /// `submit_advisor_feedback` (submission, terminal).
    SubmitAdvisorFeedback,
    /// `submit_exploration_result` (submission, terminal).
    SubmitExplorationResult,
}

impl ToolName {
    /// Every tool name, in a stable order. Used by registry-totality tests and
    /// as the canonical iteration order for default-set construction.
    pub const ALL: [ToolName; 24] = [
        ToolName::ReadFile,
        ToolName::WriteFile,
        ToolName::EditFile,
        ToolName::MultiEdit,
        ToolName::ExecCommand,
        ToolName::WriteStdin,
        ToolName::Grep,
        ToolName::Glob,
        ToolName::EnterIsolatedWorkspace,
        ToolName::ExitIsolatedWorkspace,
        ToolName::RunSubagent,
        ToolName::CheckSubagentProgress,
        ToolName::CancelSubagent,
        ToolName::AskAdvisor,
        ToolName::DelegateWorkflow,
        ToolName::CheckWorkflowStatus,
        ToolName::CancelWorkflow,
        ToolName::LoadSkillReference,
        ToolName::SubmitRootOutcome,
        ToolName::SubmitGeneratorOutcome,
        ToolName::SubmitReducerOutcome,
        ToolName::SubmitPlannerOutcome,
        ToolName::SubmitAdvisorFeedback,
        ToolName::SubmitExplorationResult,
    ];

    /// The wire string the model calls this tool by.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            ToolName::ReadFile => "read_file",
            ToolName::WriteFile => "write_file",
            ToolName::EditFile => "edit_file",
            ToolName::MultiEdit => "multi_edit",
            ToolName::ExecCommand => "exec_command",
            ToolName::WriteStdin => "write_stdin",
            ToolName::Grep => "grep",
            ToolName::Glob => "glob",
            ToolName::EnterIsolatedWorkspace => "enter_isolated_workspace",
            ToolName::ExitIsolatedWorkspace => "exit_isolated_workspace",
            ToolName::RunSubagent => "run_subagent",
            ToolName::CheckSubagentProgress => "check_subagent_progress",
            ToolName::CancelSubagent => "cancel_subagent",
            ToolName::AskAdvisor => "ask_advisor",
            ToolName::DelegateWorkflow => "delegate_workflow",
            ToolName::CheckWorkflowStatus => "check_workflow_status",
            ToolName::CancelWorkflow => "cancel_workflow",
            ToolName::LoadSkillReference => "load_skill_reference",
            ToolName::SubmitRootOutcome => "submit_root_outcome",
            ToolName::SubmitGeneratorOutcome => "submit_generator_outcome",
            ToolName::SubmitReducerOutcome => "submit_reducer_outcome",
            ToolName::SubmitPlannerOutcome => "submit_planner_outcome",
            ToolName::SubmitAdvisorFeedback => "submit_advisor_feedback",
            ToolName::SubmitExplorationResult => "submit_exploration_result",
        }
    }

    /// Parse a wire string into a [`ToolName`], or `None` when unknown.
    #[must_use]
    pub fn from_wire(value: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|name| name.as_str() == value)
    }
}

impl fmt::Display for ToolName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for ToolName {
    type Err = ();

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Self::from_wire(s).ok_or(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ALL lists every variant exactly once (no duplicates, no omissions).
    #[test]
    fn all_is_complete_and_unique() {
        let mut seen = std::collections::BTreeSet::new();
        for name in ToolName::ALL {
            assert!(seen.insert(name.as_str()), "duplicate {}", name.as_str());
        }
        assert_eq!(seen.len(), 24);
    }

    // The hand-written wire table agrees with the serde `rename_all` projection.
    #[test]
    fn as_str_matches_serde_rename() {
        for name in ToolName::ALL {
            let serde_value = serde_json::to_value(name).expect("serialize");
            assert_eq!(serde_value, serde_json::json!(name.as_str()));
            // round-trip through from_wire and serde.
            assert_eq!(ToolName::from_wire(name.as_str()), Some(name));
            let back: ToolName =
                serde_json::from_value(serde_json::json!(name.as_str())).expect("parse");
            assert_eq!(back, name);
        }
        assert_eq!(ToolName::from_wire("not_a_tool"), None);
    }
}
