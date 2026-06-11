#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum IsolatedError {
    #[error("isolated workspaces are disabled")]
    FeatureDisabled,

    #[error("invalid argument: {0}")]
    InvalidArgument(String),

    #[error("agent already has an open isolated workspace")]
    AlreadyOpen { created_at: f64, last_activity: f64 },

    #[error("agent has no open isolated workspace")]
    NotOpen,

    #[error("global isolated workspace cap reached")]
    QuotaExceeded { total_cap: u32 },

    #[error("host RAM gate refuses new isolated workspace")]
    HostRamPressure {
        required_bytes: u64,
        budget_bytes: u64,
    },

    #[error("setup failed at step {step}")]
    SetupFailed { step: String },

    #[error("isolated network unavailable: {0}")]
    NetworkUnavailable(String),
}

impl IsolatedError {
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            Self::FeatureDisabled => "feature_disabled",
            Self::InvalidArgument(_) => "invalid_argument",
            Self::AlreadyOpen { .. } => "already_open",
            Self::NotOpen => "not_open",
            Self::QuotaExceeded { .. } => "quota_exceeded",
            Self::HostRamPressure { .. } => "host_ram_pressure",
            Self::SetupFailed { .. } | Self::NetworkUnavailable(_) => "setup_failed",
        }
    }
}
