use std::collections::BTreeMap;

use eos_types::CommandSessionId;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use super::common::{SandboxRequestBase, SandboxResultBase};

/// Stdout/stderr captured from a command session.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize, JsonSchema)]
pub struct CommandOutput {
    /// Captured stdout.
    #[serde(default)]
    pub stdout: String,
    /// Captured stderr.
    #[serde(default)]
    pub stderr: String,
}

/// Run or start a managed command session.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ExecCommandRequest {
    /// Caller identity / description / invocation id.
    #[serde(flatten)]
    pub base: SandboxRequestBase,
    /// Command line to run.
    pub cmd: String,
    /// Yield window in milliseconds before returning partial output.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub yield_time_ms: Option<u32>,
    /// Command timeout in seconds.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout: Option<u32>,
    /// Cap on output tokens returned.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_output_tokens: Option<u32>,
}

/// Result of [`ExecCommandRequest`] / command-session writes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ExecCommandResult {
    /// Common result fields.
    #[serde(flatten)]
    pub base: SandboxResultBase,
    /// Session status (`success` is derived from this; `error`/`timed_out` fail).
    pub status: String,
    /// Process exit code, when the command has finished.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i32>,
    /// Captured output.
    pub output: CommandOutput,
    /// The managed command-session id, when one was opened.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub command_session_id: Option<CommandSessionId>,
    /// Per-path mutation kinds reported by the daemon.
    #[serde(default)]
    pub changed_path_kinds: BTreeMap<String, String>,
    /// Source of the mutation (daemon-reported).
    #[serde(default)]
    pub mutation_source: String,
}

/// Parsed view over the raw command status string.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CommandStatusView<'a> {
    raw: &'a str,
}

impl<'a> CommandStatusView<'a> {
    /// Create a view over raw daemon status text.
    #[must_use]
    pub const fn new(raw: &'a str) -> Self {
        Self { raw }
    }

    /// Borrow the raw status text.
    #[must_use]
    pub const fn raw(self) -> &'a str {
        self.raw
    }

    /// Known status variant, if this status is part of the current vocabulary.
    #[must_use]
    pub fn known(self) -> Option<KnownCommandStatus> {
        KnownCommandStatus::from_raw(self.raw)
    }

    /// Whether this status is a still-running command session.
    #[must_use]
    pub fn is_running(self) -> bool {
        matches!(self.known(), Some(KnownCommandStatus::Running))
    }

    /// Whether this status is a daemon-level command error.
    #[must_use]
    pub fn is_error_status(self) -> bool {
        matches!(
            self.known(),
            Some(KnownCommandStatus::Error | KnownCommandStatus::TimedOut)
        )
    }
}

/// Known command status values projected from raw daemon text.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KnownCommandStatus {
    /// Command session is still running.
    Running,
    /// Command completed successfully.
    Ok,
    /// Command failed.
    Error,
    /// Command timed out.
    TimedOut,
}

impl KnownCommandStatus {
    /// Parse a raw command status.
    #[must_use]
    pub fn from_raw(raw: &str) -> Option<Self> {
        match raw {
            "running" => Some(Self::Running),
            "ok" | "completed" => Some(Self::Ok),
            "error" => Some(Self::Error),
            "timed_out" => Some(Self::TimedOut),
            _ => None,
        }
    }

    /// Whether raw status text represents a command error.
    #[must_use]
    pub fn is_error_raw(raw: &str) -> bool {
        matches!(Self::from_raw(raw), Some(Self::Error | Self::TimedOut))
    }
}

impl ExecCommandResult {
    /// Parsed view over [`Self::status`].
    #[must_use]
    pub fn status_view(&self) -> CommandStatusView<'_> {
        CommandStatusView::new(&self.status)
    }

    /// Whether the command is still running.
    #[must_use]
    pub fn is_running(&self) -> bool {
        self.status_view().is_running()
    }

    /// Whether the raw status is an error or timeout.
    #[must_use]
    pub fn is_error_status(&self) -> bool {
        self.status_view().is_error_status()
    }

    /// Whether the daemon reports that the live command session is gone.
    #[must_use]
    pub fn is_session_not_found(&self) -> bool {
        matches!(self.status_view().known(), Some(KnownCommandStatus::Error))
            && self.output.stderr.contains("command_session_not_found")
    }
}

/// Write characters to an open command session through `api.v1.write_stdin`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ExecStdinRequest {
    /// Caller identity / description / invocation id.
    #[serde(flatten)]
    pub base: SandboxRequestBase,
    /// Target command-session id.
    pub command_session_id: CommandSessionId,
    /// Characters (stdin) to write.
    pub chars: String,
    /// Yield window in milliseconds before returning partial output.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub yield_time_ms: Option<u32>,
    /// Cap on output tokens returned.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_output_tokens: Option<u32>,
    /// Tear the session down (SIGTERM→SIGKILL) after writing — the explicit
    /// teardown channel, decoupled from `\x03`/SIGINT (sense-2 D7).
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub terminate: bool,
}

/// Cancel an open command session.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct CommandSessionCancelRequest {
    /// Caller identity / description / invocation id.
    #[serde(flatten)]
    pub base: SandboxRequestBase,
    /// Target command-session id.
    pub command_session_id: CommandSessionId,
}
