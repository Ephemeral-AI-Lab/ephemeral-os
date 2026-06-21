use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum OperationScope {
    System,
    Sandbox { sandbox_id: String },
}

impl OperationScope {
    #[must_use]
    pub const fn system() -> Self {
        Self::System
    }

    #[must_use]
    pub fn sandbox(sandbox_id: impl Into<String>) -> Self {
        Self::Sandbox {
            sandbox_id: sandbox_id.into(),
        }
    }

    #[must_use]
    pub fn sandbox_id(&self) -> Option<&str> {
        match self {
            Self::System => None,
            Self::Sandbox { sandbox_id } => Some(sandbox_id),
        }
    }

    #[must_use]
    pub fn is_system(&self) -> bool {
        matches!(self, Self::System)
    }

    #[must_use]
    pub fn is_sandbox(&self) -> bool {
        matches!(self, Self::Sandbox { .. })
    }

    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        match self {
            Self::System => Ok(()),
            Self::Sandbox { sandbox_id } if sandbox_id.trim().is_empty() => {
                Err("scope sandbox_id must be non-empty")
            }
            Self::Sandbox { .. } => Ok(()),
        }
    }
}
