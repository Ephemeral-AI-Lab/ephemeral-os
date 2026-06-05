use std::collections::BTreeMap;

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
    pub command_session_id: Option<String>,
    /// Per-path mutation kinds reported by the daemon.
    #[serde(default)]
    pub changed_path_kinds: BTreeMap<String, String>,
    /// Source of the mutation (daemon-reported).
    #[serde(default)]
    pub mutation_source: String,
}

/// Write characters to an open command session through `api.v1.write_stdin`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ExecStdinRequest {
    /// Caller identity / description / invocation id.
    #[serde(flatten)]
    pub base: SandboxRequestBase,
    /// Target command-session id.
    pub command_session_id: String,
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
    pub command_session_id: String,
}
