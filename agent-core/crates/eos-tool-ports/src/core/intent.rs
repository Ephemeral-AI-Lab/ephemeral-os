//! [`ToolIntent`] — the tool-classification intent (read-only / write / lifecycle).
//!
//! `eos-tool` owns this enum (anchor §5). It shares the three values of
//! `eos_sandbox_port::Intent` (the foreground sandbox-call intent) but is a
//! distinct, locally-owned contract; the sandbox boundary converts via
//! [`From`]/[`Into`] rather than aliasing another crate's type (GC: avoids an
//! unrecorded cross-crate ownership inversion). The lifecycle-batch predicate
//! (`runtime/dispatch.rs`) and sandbox routing both read this.

use eos_sandbox_port::Intent;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// How a tool is classified for batch-dispatch policy and sandbox routing.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum ToolIntent {
    /// Read-only operation (no mutations) — `Intent.READ_ONLY`.
    ReadOnly,
    /// Operation permitted to mutate the workspace — `Intent.WRITE_ALLOWED`.
    WriteAllowed,
    /// Workspace lifecycle operation — `Intent.LIFECYCLE`. Drives the
    /// lifecycle-batch policy (§6.6): a lifecycle call executes solo so later
    /// calls observe new routing state.
    Lifecycle,
}

impl ToolIntent {
    /// Every intent, in a stable order — the canonical iteration order, mirrored
    /// from [`ToolName::ALL`](crate::ToolName) for totality and [`from_wire`].
    ///
    /// [`from_wire`]: ToolIntent::from_wire
    pub const ALL: [ToolIntent; 3] = [
        ToolIntent::ReadOnly,
        ToolIntent::WriteAllowed,
        ToolIntent::Lifecycle,
    ];

    /// The wire string for this intent (the serde `snake_case` form).
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            ToolIntent::ReadOnly => "read_only",
            ToolIntent::WriteAllowed => "write_allowed",
            ToolIntent::Lifecycle => "lifecycle",
        }
    }

    /// Parse a wire string into a [`ToolIntent`], or `None` when unknown,
    /// reusing [`as_str`](ToolIntent::as_str) as the single source of spelling.
    #[must_use]
    pub fn from_wire(value: &str) -> Option<Self> {
        Self::ALL
            .into_iter()
            .find(|intent| intent.as_str() == value)
    }
}

impl From<Intent> for ToolIntent {
    fn from(intent: Intent) -> Self {
        match intent {
            Intent::ReadOnly => ToolIntent::ReadOnly,
            Intent::WriteAllowed => ToolIntent::WriteAllowed,
            Intent::Lifecycle => ToolIntent::Lifecycle,
        }
    }
}

impl From<ToolIntent> for Intent {
    fn from(intent: ToolIntent) -> Self {
        match intent {
            ToolIntent::ReadOnly => Intent::ReadOnly,
            ToolIntent::WriteAllowed => Intent::WriteAllowed,
            ToolIntent::Lifecycle => Intent::Lifecycle,
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap permitted in tests (err-no-unwrap-prod)
    use super::*;

    #[test]
    fn round_trips_with_sandbox_intent() {
        for intent in [
            ToolIntent::ReadOnly,
            ToolIntent::WriteAllowed,
            ToolIntent::Lifecycle,
        ] {
            let sandbox: Intent = intent.into();
            assert_eq!(ToolIntent::from(sandbox), intent);
            assert_eq!(intent.as_str(), sandbox.as_wire());
        }
    }

    #[test]
    fn from_wire_round_trips_and_rejects_unknown() {
        for intent in ToolIntent::ALL {
            assert_eq!(ToolIntent::from_wire(intent.as_str()), Some(intent));
        }
        assert_eq!(ToolIntent::from_wire("nope"), None);
    }

    #[test]
    fn wire_values_match_rust() {
        assert_eq!(
            serde_json::to_value(ToolIntent::ReadOnly).unwrap(),
            "read_only"
        );
        assert_eq!(
            serde_json::to_value(ToolIntent::WriteAllowed).unwrap(),
            "write_allowed"
        );
        assert_eq!(
            serde_json::to_value(ToolIntent::Lifecycle).unwrap(),
            "lifecycle"
        );
    }
}
