//! Shared verb intent for the wire protocol.
//!
//! [`Intent`] is the single verb-classification enum (serialized as its
//! snake_case `.value`).

use serde::{Deserialize, Serialize};

/// The single enum in the verb model; serialized as its `.value` string.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Intent {
    /// `"read_only"`
    ReadOnly,
    /// `"write_allowed"`
    WriteAllowed,
    /// `"lifecycle"`
    Lifecycle,
}

#[cfg(test)]
#[path = "../../tests/unit/protocol/intent.rs"]
mod tests;
