//! Shared hook declarations.
//!
//! This module intentionally carries hook identity and config spelling only.
//! Hook execution policy lives with engine tool-call dispatch.

use crate::ToolName;

/// One configured pre-hook.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum Hook {
    /// Refuse a terminal / lifecycle tool while background sessions are open.
    RequireNoBackgroundSessions {
        /// The protected tool.
        tool: ToolName,
    },
    /// Refuse a main-role terminal that lacks prior advisor approval.
    AdvisorApproval {
        /// The protected tool.
        tool: ToolName,
    },
    /// Refuse git working-tree / metadata mutation shell commands.
    DestructiveGitShell {
        /// The protected tool.
        tool: ToolName,
    },
    /// Refuse destructive filesystem shell commands.
    DestructiveShell {
        /// The protected tool.
        tool: ToolName,
    },
    /// Refuse `ask_advisor` while an isolated workspace is open.
    BlockInIsolatedMode {
        /// The protected tool.
        tool: ToolName,
    },
}

impl Hook {
    /// The protected tool name.
    #[must_use]
    pub const fn tool(self) -> ToolName {
        match self {
            Hook::RequireNoBackgroundSessions { tool }
            | Hook::AdvisorApproval { tool }
            | Hook::DestructiveGitShell { tool }
            | Hook::DestructiveShell { tool }
            | Hook::BlockInIsolatedMode { tool } => tool,
        }
    }

    /// The canonical config token for this hook.
    #[must_use]
    pub const fn config_token(self) -> &'static str {
        match self {
            Hook::RequireNoBackgroundSessions { .. } => "no_background_sessions",
            Hook::AdvisorApproval { .. } => "advisor_approval",
            Hook::DestructiveGitShell { .. } => "destructive_git_shell",
            Hook::DestructiveShell { .. } => "destructive_shell",
            Hook::BlockInIsolatedMode { .. } => "block_in_isolated_mode",
        }
    }

    /// The Rust hook `name` used in hook failure metadata.
    #[must_use]
    pub fn hook_name(self) -> String {
        let tool = self.tool().as_str();
        match self {
            Hook::RequireNoBackgroundSessions { .. } => format!("no_background_sessions:{tool}"),
            Hook::AdvisorApproval { .. } => format!("advisor_approval:{tool}"),
            Hook::DestructiveGitShell { .. } => format!("sandbox_shell:destructive_git:{tool}"),
            Hook::DestructiveShell { .. } => format!("sandbox_shell:destructive_shell:{tool}"),
            Hook::BlockInIsolatedMode { .. } => format!("block_in_isolated_mode:{tool}"),
        }
    }
}
