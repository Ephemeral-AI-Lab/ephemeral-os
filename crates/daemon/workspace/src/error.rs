use crate::model::{CallerId, WorkspaceId};

#[derive(Debug)]
pub enum WorkspaceError {
    InvalidRequest {
        field: &'static str,
        message: String,
    },

    FeatureDisabled,

    AlreadyOpen {
        owner: CallerId,
        workspace_id: WorkspaceId,
    },

    NotOpen {
        owner: CallerId,
    },

    ActiveCommands {
        owner: CallerId,
        active_commands: usize,
    },

    QuotaExceeded {
        total_cap: u32,
    },

    ResourcePressure {
        required_bytes: u64,
        budget_bytes: u64,
    },

    SnapshotAcquire {
        source: String,
    },

    Setup {
        step: String,
    },

    Network {
        message: String,
    },

    Command {
        message: String,
    },

    Capture {
        message: String,
    },

    Publish {
        message: String,
    },
}

impl std::fmt::Display for WorkspaceError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidRequest { field, message } => {
                write!(formatter, "invalid request for {field}: {message}")
            }
            Self::FeatureDisabled => write!(formatter, "workspace feature is disabled"),
            Self::AlreadyOpen { owner, .. } => {
                write!(formatter, "workspace already open for {owner:?}")
            }
            Self::NotOpen { owner } => write!(formatter, "workspace is not open for {owner:?}"),
            Self::ActiveCommands { .. } => {
                write!(
                    formatter,
                    "cannot change workspace while commands are active"
                )
            }
            Self::QuotaExceeded { total_cap } => {
                write!(formatter, "workspace quota exceeded: {total_cap}")
            }
            Self::ResourcePressure {
                required_bytes,
                budget_bytes,
            } => write!(
                formatter,
                "resource pressure: required {required_bytes}, budget {budget_bytes}"
            ),
            Self::SnapshotAcquire { source } => {
                write!(formatter, "snapshot acquire failed: {source}")
            }
            Self::Setup { step } => write!(formatter, "workspace setup failed at {step}"),
            Self::Network { message } => write!(formatter, "network setup failed: {message}"),
            Self::Command { message } => write!(formatter, "command failed: {message}"),
            Self::Capture { message } => write!(formatter, "capture failed: {message}"),
            Self::Publish { message } => write!(formatter, "publish failed: {message}"),
        }
    }
}

impl std::error::Error for WorkspaceError {}
