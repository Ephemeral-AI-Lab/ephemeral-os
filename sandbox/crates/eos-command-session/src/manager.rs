use std::sync::Arc;
use std::time::Instant;

use eos_workspace_api::CommandWorkspacePolicy;

use crate::event::{CommandSessionFinished, CommandSessionStarted};
use crate::registry::CommandSessionCompletion;
use crate::session::CommandSessionSpec;
use crate::{
    CancelCommandSession, CollectCompleted, CollectCompletedResponse, CommandResponse,
    CommandSession, CommandSessionConfig, CommandSessionError, CommandSessionEventSink,
    CommandSessionRegistry, DynCommandWorkspacePolicy, NoopCommandSessionEventSink,
    StartCommandSession, WriteStdin,
};

pub struct CommandSessionManager {
    config: CommandSessionConfig,
    registry: Arc<CommandSessionRegistry>,
    events: Arc<dyn CommandSessionEventSink>,
}

impl CommandSessionManager {
    #[must_use]
    pub fn new(config: CommandSessionConfig) -> Self {
        Self {
            config,
            registry: Arc::new(CommandSessionRegistry::new()),
            events: Arc::new(NoopCommandSessionEventSink),
        }
    }

    #[must_use]
    pub fn with_event_sink<E>(config: CommandSessionConfig, events: E) -> Self
    where
        E: CommandSessionEventSink + 'static,
    {
        Self {
            config,
            registry: Arc::new(CommandSessionRegistry::new()),
            events: Arc::new(events),
        }
    }

    #[must_use]
    pub fn registry(&self) -> &Arc<CommandSessionRegistry> {
        &self.registry
    }

    pub fn start<P>(
        &self,
        request: StartCommandSession,
        policy: P,
    ) -> Result<CommandResponse, CommandSessionError>
    where
        P: CommandWorkspacePolicy + 'static,
    {
        self.start_boxed(request, Box::new(policy))
    }

    pub fn start_boxed(
        &self,
        request: StartCommandSession,
        policy: DynCommandWorkspacePolicy,
    ) -> Result<CommandResponse, CommandSessionError> {
        if request.cmd.trim().is_empty() {
            return Err(CommandSessionError::InvalidRequest(
                "cmd must be non-empty".to_owned(),
            ));
        }
        let id = self.registry.next_id();
        let prepared = policy.prepare_command_workspace(request.prepare_request(id.clone()))?;
        let session = Arc::new(CommandSession::new(
            CommandSessionSpec {
                id: id.clone(),
                caller_id: request.caller_id,
                command: request.cmd,
                workspace_mode: prepared.mode,
                timeout_seconds: request.timeout_seconds,
                finalize_context: prepared.finalize_context,
            },
            policy,
            &self.config,
        ));
        self.events.session_started(CommandSessionStarted {
            command_session_id: id.clone(),
            caller_id: session.caller_id().to_owned(),
            workspace_mode: session.workspace_mode(),
        });
        self.registry.insert(Arc::clone(&session));
        Ok(CommandResponse::running(
            id,
            session.read_model_output(request.max_output_tokens),
        ))
    }

    pub fn write_stdin(&self, request: WriteStdin) -> Result<CommandResponse, CommandSessionError> {
        let Some(session) = self.registry.get(&request.command_session_id) else {
            return self
                .registry
                .take_completed_result(&request.command_session_id)
                .ok_or(CommandSessionError::NotFound(request.command_session_id));
        };
        if !request.chars.is_empty() {
            session.append_output(request.chars);
        }
        if request.terminate {
            return self.finish_session(session, "cancelled", Some(130), true);
        }
        Ok(CommandResponse::running(
            request.command_session_id,
            session.read_model_output(request.max_output_tokens),
        ))
    }

    pub fn cancel(
        &self,
        request: CancelCommandSession,
    ) -> Result<CommandResponse, CommandSessionError> {
        let Some(session) = self.registry.get(&request.command_session_id) else {
            return self
                .registry
                .take_completed_result(&request.command_session_id)
                .ok_or(CommandSessionError::NotFound(request.command_session_id));
        };
        let _ = request.max_output_tokens;
        self.finish_session(session, "cancelled", Some(130), true)
    }

    #[must_use]
    pub fn count_by_caller(&self, caller_id: Option<&str>) -> usize {
        self.registry.count_by_caller(caller_id)
    }

    #[must_use]
    pub fn collect_completed(&self, request: &CollectCompleted) -> CollectCompletedResponse {
        self.registry.collect_completed(request)
    }

    #[must_use]
    pub fn sweep_expired(&self, now: Instant) -> SweepReport {
        let mut expired = 0;
        for session in self.registry.live() {
            if session.is_expired(now) && self.registry.remove(session.id()).is_some() {
                expired += 1;
            }
        }
        SweepReport {
            expired,
            live: self.registry.live().len(),
        }
    }

    fn finish_session(
        &self,
        session: Arc<CommandSession>,
        status: &str,
        exit_code: Option<i64>,
        include_session_id: bool,
    ) -> Result<CommandResponse, CommandSessionError> {
        let result = session.finalize(status, exit_code, include_session_id)?;
        let notification_result = result
            .clone()
            .with_stdout(session.read_notification_output(None));
        let command_session_id = session.id().to_owned();
        let caller_id = session.caller_id().to_owned();
        let command = session.command().to_owned();
        let workspace_mode = session.workspace_mode();
        self.registry.remove(&command_session_id);
        self.events.session_finished(CommandSessionFinished {
            command_session_id: command_session_id.clone(),
            caller_id: caller_id.clone(),
            workspace_mode,
            status: result.status.clone(),
        });
        self.registry.push_completed(CommandSessionCompletion {
            command_session_id,
            caller_id,
            command,
            result: result.clone(),
            notification_result,
        });
        Ok(result)
    }
}

impl Default for CommandSessionManager {
    fn default() -> Self {
        Self::new(CommandSessionConfig::default())
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct SweepReport {
    pub expired: usize,
    pub live: usize,
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::time::Duration;

    use eos_workspace_api::{
        FinalizeCommandRequest, PrepareCommandRequest, PreparedCommandWorkspace, WorkspaceApiError,
        WorkspaceCommandOutcome, WorkspaceMode,
    };
    use serde_json::{json, Value};

    use super::*;

    struct ExpiringPolicy;

    impl CommandWorkspacePolicy for ExpiringPolicy {
        fn prepare_command_workspace(
            &self,
            request: PrepareCommandRequest,
        ) -> Result<PreparedCommandWorkspace, WorkspaceApiError> {
            let session_dir = PathBuf::from(format!("/sessions/{}", request.command_session_id));
            Ok(PreparedCommandWorkspace {
                mode: WorkspaceMode::default(),
                run_request: json!({ "cmd": request.cmd }),
                request_path: session_dir.join("runner-request.json"),
                output_path: session_dir.join("runner-result.json"),
                final_path: session_dir.join("final.json"),
                session_dir: session_dir.clone(),
                transcript_path: session_dir.join("transcript.log"),
                finalize_context: Value::Null,
            })
        }

        fn finalize_command_workspace(
            &self,
            request: FinalizeCommandRequest,
        ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError> {
            Ok(WorkspaceCommandOutcome {
                mode: WorkspaceMode::default(),
                success: request.status == "ok",
                status: request.status,
                exit_code: request.exit_code,
                stdout: request.stdout,
                stderr: request.stderr,
                command_session_id: request.command_session_id,
                changed_paths: Vec::new(),
                changed_path_kinds: Default::default(),
                mutation_source: "test".to_owned(),
                conflict: None,
                conflict_reason: None,
                timings: Default::default(),
                metadata: Value::Null,
            })
        }
    }

    #[test]
    fn manager_registers_counts_and_sweeps_sessions() {
        let manager = CommandSessionManager::default();
        let started = manager
            .start(
                StartCommandSession {
                    invocation_id: "inv".to_owned(),
                    caller_id: "caller".to_owned(),
                    cmd: "sleep 1".to_owned(),
                    timeout_seconds: Some(0.001),
                    yield_time_ms: 1000,
                    max_output_tokens: None,
                },
                ExpiringPolicy,
            )
            .unwrap_or_else(|error| panic!("start session: {error}"));
        let id = started
            .command_session_id
            .unwrap_or_else(|| panic!("running session id"));
        let session = manager
            .registry()
            .get(&id)
            .unwrap_or_else(|| panic!("registered session"));

        assert_eq!(manager.count_by_caller(Some("caller")), 1);

        let report = manager.sweep_expired(session.started_at() + Duration::from_millis(2));

        assert_eq!(report.expired, 1);
        assert_eq!(report.live, 0);
    }
}
