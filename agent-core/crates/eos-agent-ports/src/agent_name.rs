//! Agent profile name DTO.

use std::fmt;

/// Agent profile name used by agent-run ports.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct AgentName(String);

impl AgentName {
    /// Build a non-empty trimmed agent name.
    ///
    /// # Errors
    /// Returns [`AgentNameError::Empty`] when the trimmed value is empty.
    pub fn new(value: impl AsRef<str>) -> Result<Self, AgentNameError> {
        let value = value.as_ref().trim();
        if value.is_empty() {
            return Err(AgentNameError::Empty);
        }
        Ok(Self(value.to_owned()))
    }

    /// Borrow the name as a string slice.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for AgentName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Agent-name validation error.
#[derive(Debug, Clone, Copy, thiserror::Error, PartialEq, Eq)]
#[non_exhaustive]
pub enum AgentNameError {
    /// Agent names must not be empty after trimming.
    #[error("agent name must be non-empty")]
    Empty,
}
