use serde::{Deserialize, Serialize};

/// Workspace mode that produced a result.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkspaceMode {
    /// Shared publish-capable workspace path.
    #[default]
    Ephemeral,
    /// Agent-private no-publish workspace path.
    Isolated,
}

impl WorkspaceMode {
    /// Stable daemon/API string for this mode.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ephemeral => "ephemeral",
            Self::Isolated => "isolated",
        }
    }
}
