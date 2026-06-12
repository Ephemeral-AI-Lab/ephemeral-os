use std::path::PathBuf;

use serde::Deserialize;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CommandConfig {
    pub scratch_root: PathBuf,
    pub default_yield_time_ms: u64,
    pub default_timeout_s: u64,
    pub quiet_ms: u64,
    pub cancel_wait_ms: u64,
    pub output_drain_grace_ms: u64,
    pub max_command_s: u64,
    pub transcript_timestamp_timezone: String,
}

impl Default for CommandConfig {
    fn default() -> Self {
        Self {
            scratch_root: PathBuf::from("/eos/scratch/commands"),
            default_yield_time_ms: 1000,
            default_timeout_s: 600,
            quiet_ms: 50,
            cancel_wait_ms: 500,
            output_drain_grace_ms: 500,
            max_command_s: 6 * 60 * 60,
            transcript_timestamp_timezone: "UTC".to_owned(),
        }
    }
}
