//! Command-session service over the sandbox transport.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{AgentRunId, CommandSessionId, JsonObject, SandboxId};

use crate::{
    cancel_command_session, cancel_workspace_runs_by_caller_id, collect_command_completions,
    exec_command, exec_stdin, read_command_progress, CommandSessionCancelRequest,
    ExecCommandRequest, ExecCommandResult, ExecStdinRequest, ReadCommandProgressRequest,
    SandboxPortError, SandboxTransport,
};

/// Sandbox command API used by tools and engine-local command-session managers.
#[async_trait]
pub trait SandboxCommandApi: Send + Sync {
    /// Run or start a managed command session.
    async fn exec_command(
        &self,
        sandbox_id: &SandboxId,
        request: &ExecCommandRequest,
    ) -> Result<ExecCommandResult, SandboxPortError>;

    /// Write stdin to an open command session.
    async fn write_stdin(
        &self,
        sandbox_id: &SandboxId,
        request: &ExecStdinRequest,
    ) -> Result<ExecCommandResult, SandboxPortError>;

    /// Read command progress.
    async fn read_command_progress(
        &self,
        sandbox_id: &SandboxId,
        request: &ReadCommandProgressRequest,
    ) -> Result<ExecCommandResult, SandboxPortError>;

    /// Cancel one command session.
    async fn cancel_command_session(
        &self,
        sandbox_id: &SandboxId,
        request: &CommandSessionCancelRequest,
    ) -> Result<ExecCommandResult, SandboxPortError>;

    /// Collect completed background command sessions for one caller.
    async fn collect_completed_commands(
        &self,
        sandbox_id: &SandboxId,
        caller_id: &AgentRunId,
        command_session_ids: &[CommandSessionId],
    ) -> Result<Vec<JsonObject>, SandboxPortError>;

    /// Cancel every command/workspace run owned by one caller.
    async fn cancel_commands_for_run(
        &self,
        sandbox_id: &SandboxId,
        caller_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), SandboxPortError>;
}

/// Transport-backed [`SandboxCommandApi`].
#[derive(Clone)]
pub struct SandboxCommandService {
    transport: Arc<dyn SandboxTransport>,
}

impl std::fmt::Debug for SandboxCommandService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SandboxCommandService")
            .finish_non_exhaustive()
    }
}

impl SandboxCommandService {
    /// Build the command service from the daemon transport.
    #[must_use]
    pub fn new(transport: Arc<dyn SandboxTransport>) -> Self {
        Self { transport }
    }
}

#[async_trait]
impl SandboxCommandApi for SandboxCommandService {
    async fn exec_command(
        &self,
        sandbox_id: &SandboxId,
        request: &ExecCommandRequest,
    ) -> Result<ExecCommandResult, SandboxPortError> {
        exec_command(&*self.transport, sandbox_id, request).await
    }

    async fn write_stdin(
        &self,
        sandbox_id: &SandboxId,
        request: &ExecStdinRequest,
    ) -> Result<ExecCommandResult, SandboxPortError> {
        exec_stdin(&*self.transport, sandbox_id, request).await
    }

    async fn read_command_progress(
        &self,
        sandbox_id: &SandboxId,
        request: &ReadCommandProgressRequest,
    ) -> Result<ExecCommandResult, SandboxPortError> {
        read_command_progress(&*self.transport, sandbox_id, request).await
    }

    async fn cancel_command_session(
        &self,
        sandbox_id: &SandboxId,
        request: &CommandSessionCancelRequest,
    ) -> Result<ExecCommandResult, SandboxPortError> {
        cancel_command_session(&*self.transport, sandbox_id, request).await
    }

    async fn collect_completed_commands(
        &self,
        sandbox_id: &SandboxId,
        caller_id: &AgentRunId,
        command_session_ids: &[CommandSessionId],
    ) -> Result<Vec<JsonObject>, SandboxPortError> {
        let ids = command_session_ids
            .iter()
            .map(|id| id.as_str().to_owned())
            .collect::<Vec<_>>();
        collect_command_completions(&*self.transport, sandbox_id, caller_id.as_str(), &ids).await
    }

    async fn cancel_commands_for_run(
        &self,
        sandbox_id: &SandboxId,
        caller_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), SandboxPortError> {
        cancel_workspace_runs_by_caller_id(&*self.transport, sandbox_id, caller_id.as_str())
            .await
            .map(|_| ())
            .map_err(|err| {
                tracing::warn!(
                    error = %err,
                    sandbox_id = sandbox_id.as_str(),
                    caller_id = caller_id.as_str(),
                    reason,
                    "per-caller workspace-run cancellation failed"
                );
                err
            })
    }
}
