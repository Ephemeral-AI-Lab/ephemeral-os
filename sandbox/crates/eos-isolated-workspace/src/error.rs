//! Isolated-workspace lifecycle error type.
//!
//! `kind()` maps onto the daemon RPC wire error kind, mirroring the Python
//! `IsolatedWorkspaceError.kind` string (`_control_plane/types.py:87-97`).

use std::path::PathBuf;

/// Lifecycle error for the enter/exit isolated-workspace flow.
///
/// Each variant's `kind()` reproduces the Python `kind` string fed onto the
/// daemon RPC response envelope.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum IsolatedError {
    /// `feature_disabled` — pipeline not initialized / feature flag off.
    #[error("isolated workspaces are disabled")]
    FeatureDisabled,

    /// `invalid_argument` — a required argument was empty or malformed.
    #[error("invalid argument: {0}")]
    InvalidArgument(String),

    /// `already_open` — the agent already holds an open isolated workspace.
    #[error("agent already has an open isolated workspace")]
    AlreadyOpen {
        /// Existing handle creation timestamp.
        created_at: f64,
        /// Existing handle last-activity timestamp.
        last_activity: f64,
    },

    /// `not_open` — the agent has no open isolated workspace.
    #[error("agent has no open isolated workspace")]
    NotOpen,

    /// `quota_exceeded` — the global concurrent-workspace cap is reached.
    #[error("global isolated workspace cap reached")]
    QuotaExceeded {
        /// Configured global isolated-workspace cap.
        total_cap: u32,
    },

    /// `host_ram_pressure` — projected upperdir reservation exceeds host RAM budget.
    #[error("host RAM gate refuses new isolated workspace")]
    HostRamPressure {
        /// Bytes required for the next workspace admission.
        required_bytes: u64,
        /// Bytes admitted by the current host-memory budget.
        budget_bytes: u64,
    },

    /// `setup_timeout` — a setup phase exceeded its deadline (rollback runs).
    #[error("setup timed out at step {step}")]
    SetupTimeout {
        /// The pipeline phase that timed out.
        step: String,
    },

    /// `setup_failed` — a setup phase failed (rollback runs).
    #[error("setup failed at step {step}")]
    SetupFailed {
        /// The pipeline phase that failed.
        step: String,
    },

    /// Linux network primitives (`ip`/`nft`/`CAP_NET_ADMIN`) unavailable.
    #[error("isolated network unavailable: {0}")]
    NetworkUnavailable(String),

    /// An audit-sink write to the JSONL path failed.
    #[error("audit sink write failed at {path}")]
    AuditWrite {
        /// The configured JSONL audit path.
        path: PathBuf,
        /// Underlying I/O error.
        #[source]
        source: std::io::Error,
    },
}

impl IsolatedError {
    /// The wire error `kind` string this error maps to.
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            Self::FeatureDisabled => "feature_disabled",
            Self::InvalidArgument(_) => "invalid_argument",
            Self::AlreadyOpen { .. } => "already_open",
            Self::NotOpen => "not_open",
            Self::QuotaExceeded { .. } => "quota_exceeded",
            Self::HostRamPressure { .. } => "host_ram_pressure",
            Self::SetupTimeout { .. } => "setup_timeout",
            Self::SetupFailed { .. } | Self::NetworkUnavailable(_) => "setup_failed",
            Self::AuditWrite { .. } => "internal_error",
        }
    }
}
