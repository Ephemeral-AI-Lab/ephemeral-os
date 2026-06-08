use std::sync::Arc;

use async_trait::async_trait;
use eos_ports::{CommandServicePort, Sealed, ToolError};
use eos_sandbox_port::{
    cancel_command_session, cancel_workspace_runs_by_caller_id, collect_command_completions,
    exec_command, exec_stdin, read_command_progress, CommandSessionCancelRequest,
    ExecCommandRequest, ExecCommandResult, ExecStdinRequest, ReadCommandProgressRequest,
    SandboxTransport,
};
use eos_types::{AgentRunId, CommandSessionId, JsonObject, SandboxId};

#[derive(Clone)]
pub struct CommandService {
    transport: Arc<dyn SandboxTransport>,
}

impl std::fmt::Debug for CommandService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CommandService").finish_non_exhaustive()
    }
}

impl CommandService {
    #[must_use]
    pub fn new(transport: Arc<dyn SandboxTransport>) -> Self {
        Self { transport }
    }
}

impl Sealed for CommandService {}

#[async_trait]
impl CommandServicePort for CommandService {
    async fn exec_command(
        &self,
        sandbox_id: &SandboxId,
        request: &ExecCommandRequest,
    ) -> Result<ExecCommandResult, ToolError> {
        exec_command(&*self.transport, sandbox_id, request)
            .await
            .map_err(ToolError::Sandbox)
    }

    async fn write_stdin(
        &self,
        sandbox_id: &SandboxId,
        request: &ExecStdinRequest,
    ) -> Result<ExecCommandResult, ToolError> {
        exec_stdin(&*self.transport, sandbox_id, request)
            .await
            .map_err(ToolError::Sandbox)
    }

    async fn read_command_progress(
        &self,
        sandbox_id: &SandboxId,
        request: &ReadCommandProgressRequest,
    ) -> Result<ExecCommandResult, ToolError> {
        read_command_progress(&*self.transport, sandbox_id, request)
            .await
            .map_err(ToolError::Sandbox)
    }

    async fn cancel_command_session(
        &self,
        sandbox_id: &SandboxId,
        request: &CommandSessionCancelRequest,
    ) -> Result<ExecCommandResult, ToolError> {
        cancel_command_session(&*self.transport, sandbox_id, request)
            .await
            .map_err(ToolError::Sandbox)
    }

    async fn collect_completed_commands(
        &self,
        sandbox_id: &SandboxId,
        caller_id: &AgentRunId,
        command_session_ids: &[CommandSessionId],
    ) -> Result<Vec<JsonObject>, ToolError> {
        let ids = command_session_ids
            .iter()
            .map(|id| id.as_str().to_owned())
            .collect::<Vec<_>>();
        collect_command_completions(&*self.transport, sandbox_id, caller_id.as_str(), &ids)
            .await
            .map_err(ToolError::Sandbox)
    }

    async fn cancel_commands_for_run(
        &self,
        sandbox_id: &SandboxId,
        caller_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), ToolError> {
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
                ToolError::Sandbox(err)
            })
    }
}
