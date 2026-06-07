//! [`CommandSessionSupervisorPort`] implementation on
//! [`BackgroundSupervisorHandle`]: the `exec_command` / `write_stdin` /
//! `read_command_progress` tools call through here to register a background
//! command session and to recover a terminal result across the heartbeat race.
//! All state and the daemon completion poll live on the run's
//! [`CommandSessionLane`](super::lanes::CommandSessionLane); this is a thin
//! delegating surface bound to one agent run (`owner_agent_run_id == caller_id`).

use async_trait::async_trait;
use eos_tools::ports::CommandSessionSupervisorPort;
use eos_types::{CommandSessionId, SandboxId};
use serde_json::Value;

use super::handle::BackgroundSupervisorHandle;

#[async_trait]
impl CommandSessionSupervisorPort for BackgroundSupervisorHandle {
    async fn register(
        &self,
        command_session_id: &CommandSessionId,
        sandbox_id: &SandboxId,
        command: &str,
    ) {
        self.commands()
            .register(command_session_id, sandbox_id, command)
            .await;
    }

    async fn command_session_result(&self, command_session_id: &CommandSessionId) -> Option<Value> {
        self.commands()
            .command_session_result(command_session_id)
            .await
    }

    async fn mark_command_session_reported(
        &self,
        command_session_id: &CommandSessionId,
        result: Value,
    ) {
        self.commands()
            .mark_command_session_reported(command_session_id, result)
            .await;
    }

    async fn command_session_already_reported(
        &self,
        command_session_id: &CommandSessionId,
    ) -> bool {
        self.commands()
            .command_session_already_reported(command_session_id)
            .await
    }
}
