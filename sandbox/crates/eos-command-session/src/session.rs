use std::sync::Arc;
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use eos_workspace_api::{FinalizeCommandRequest, WorkspaceMode};
use serde_json::Value;

use crate::{
    CommandResponse, CommandSessionConfig, CommandSessionError, CommandSessionOutput,
    CommandSessionOutputCursor, DynCommandWorkspacePolicy,
};

pub struct CommandSession {
    id: String,
    agent_id: String,
    command: String,
    workspace_mode: WorkspaceMode,
    output: Arc<CommandSessionOutput>,
    model_cursor: Mutex<CommandSessionOutputCursor>,
    notification_cursor: Mutex<CommandSessionOutputCursor>,
    policy: Mutex<Option<DynCommandWorkspacePolicy>>,
    finalize_context: Value,
    finalized: Mutex<Option<CommandResponse>>,
    started_at: Instant,
    timeout: Option<Duration>,
}

impl CommandSession {
    #[must_use]
    pub fn new(
        id: String,
        agent_id: String,
        command: String,
        workspace_mode: WorkspaceMode,
        timeout_seconds: Option<f64>,
        policy: DynCommandWorkspacePolicy,
        finalize_context: Value,
        config: &CommandSessionConfig,
    ) -> Self {
        Self {
            id,
            agent_id,
            command,
            workspace_mode,
            output: Arc::new(CommandSessionOutput::new(config)),
            model_cursor: Mutex::new(CommandSessionOutputCursor::default()),
            notification_cursor: Mutex::new(CommandSessionOutputCursor::default()),
            policy: Mutex::new(Some(policy)),
            finalize_context,
            finalized: Mutex::new(None),
            started_at: Instant::now(),
            timeout: timeout_seconds.and_then(duration_from_secs_f64),
        }
    }

    #[must_use]
    pub fn id(&self) -> &str {
        &self.id
    }

    #[must_use]
    pub fn agent_id(&self) -> &str {
        &self.agent_id
    }

    #[must_use]
    pub fn command(&self) -> &str {
        &self.command
    }

    #[must_use]
    pub const fn workspace_mode(&self) -> WorkspaceMode {
        self.workspace_mode
    }

    #[must_use]
    pub fn output(&self) -> &Arc<CommandSessionOutput> {
        &self.output
    }

    #[must_use]
    pub fn finalize_context(&self) -> &Value {
        &self.finalize_context
    }

    pub fn append_output(&self, text: String) {
        self.output.append(text);
    }

    #[must_use]
    pub fn read_model_output(&self, max_tokens: Option<u64>) -> String {
        let mut cursor = lock(&self.model_cursor);
        self.output.read_since(&mut cursor, max_tokens)
    }

    #[must_use]
    pub fn read_notification_output(&self, max_tokens: Option<u64>) -> String {
        let mut cursor = lock(&self.notification_cursor);
        self.output.read_since(&mut cursor, max_tokens)
    }

    #[must_use]
    pub const fn started_at(&self) -> Instant {
        self.started_at
    }

    #[must_use]
    pub const fn timeout(&self) -> Option<Duration> {
        self.timeout
    }

    #[must_use]
    pub fn is_expired(&self, now: Instant) -> bool {
        self.timeout
            .is_some_and(|timeout| now.duration_since(self.started_at) >= timeout)
    }

    pub fn finalize(
        &self,
        status: &str,
        exit_code: Option<i64>,
        include_session_id: bool,
    ) -> Result<CommandResponse, CommandSessionError> {
        let mut finalized = lock(&self.finalized);
        if let Some(response) = finalized.as_ref() {
            return Ok(response.clone());
        }
        let policy = lock(&self.policy);
        let policy = policy.as_ref().ok_or_else(|| {
            CommandSessionError::Unsupported("command session has no workspace policy".to_owned())
        })?;
        let outcome = policy.finalize_command_workspace(FinalizeCommandRequest {
            finalize_context: self.finalize_context.clone(),
            runner_result: None,
            command_elapsed_s: self.started_at.elapsed().as_secs_f64(),
            spool_truncated: self.output.spool_truncated(),
            status: status.to_owned(),
            exit_code,
            stdout: self.output.all_recent(None),
            stderr: String::new(),
            command_session_id: include_session_id.then(|| self.id.clone()),
        })?;
        let response = CommandResponse::from_workspace_outcome(outcome);
        *finalized = Some(response.clone());
        Ok(response)
    }
}

fn duration_from_secs_f64(seconds: f64) -> Option<Duration> {
    if seconds.is_finite() && seconds > 0.0 {
        Some(Duration::from_secs_f64(seconds))
    } else {
        None
    }
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

#[cfg(test)]
mod tests {
    use super::*;

    struct NoopPolicy;

    impl eos_workspace_api::CommandWorkspacePolicy for NoopPolicy {
        fn prepare_command_workspace(
            &self,
            _request: eos_workspace_api::PrepareCommandRequest,
        ) -> Result<eos_workspace_api::PreparedCommandWorkspace, eos_workspace_api::WorkspaceApiError>
        {
            unreachable!("session test does not prepare")
        }

        fn finalize_command_workspace(
            &self,
            _request: eos_workspace_api::FinalizeCommandRequest,
        ) -> Result<eos_workspace_api::WorkspaceCommandOutcome, eos_workspace_api::WorkspaceApiError>
        {
            unreachable!("session test does not finalize")
        }
    }

    #[test]
    fn session_exposes_identity_and_expiry() {
        let config = CommandSessionConfig::default();
        let session = CommandSession::new(
            "cmd_1".to_owned(),
            "agent".to_owned(),
            "echo ok".to_owned(),
            WorkspaceMode::default(),
            Some(0.001),
            Box::new(NoopPolicy),
            Value::Null,
            &config,
        );

        assert_eq!(session.id(), "cmd_1");
        assert_eq!(session.agent_id(), "agent");
        assert_eq!(session.command(), "echo ok");
        assert_eq!(session.workspace_mode(), WorkspaceMode::default());
        assert!(session.is_expired(session.started_at() + Duration::from_millis(2)));
    }
}
