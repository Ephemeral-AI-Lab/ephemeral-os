// The workspace-run manager is the Linux PTY/overlay orchestration. On non-Linux
// the daemon serves command-session ops as stubs, so the manager is dead there —
// it stays compiled for the scaffold unit tests and a uniform module tree.
#![cfg_attr(not(target_os = "linux"), allow(dead_code))]

use std::sync::Arc;
#[cfg(target_os = "linux")]
use std::time::Duration;
use std::time::Instant;

#[cfg(target_os = "linux")]
use eos_command_session::process::spawn_current_exe_ns_runner;
#[cfg(target_os = "linux")]
use eos_command_session::ReapedCommand;
#[cfg(target_os = "linux")]
use eos_command_session::RunningCommandSessionParts;
#[cfg(target_os = "linux")]
use eos_command_session::{wait_for_yield, WaitOutcome};
use eos_command_session::{
    CancelCommandSession, CollectCompleted, CollectCompletedResponse, CommandResponse,
    CommandSession, CommandSessionCompletion, CommandSessionConfig, CommandSessionError,
    CommandSessionSpec, DynCommandWorkspacePolicy, ReadCommandProgress, StartCommandSession,
    WriteStdin,
};
#[cfg(not(target_os = "linux"))]
use eos_workspace_api::CommandWorkspacePolicy;
use eos_workspace_api::FinalizeCommandRequest;

use super::registry::{RunSession, WorkspaceRunKind, WorkspaceRunRegistry};

pub struct WorkspaceRunManager {
    // `config` drives only the Linux PTY/overlay paths (spawn, yield/cancel
    // waits, sweep deadlines); the non-Linux scaffold needs no config.
    #[cfg_attr(not(target_os = "linux"), allow(dead_code))]
    config: CommandSessionConfig,
    registry: Arc<WorkspaceRunRegistry>,
}

impl WorkspaceRunManager {
    #[must_use]
    pub fn new(config: CommandSessionConfig) -> Self {
        Self {
            config,
            registry: Arc::new(WorkspaceRunRegistry::new()),
        }
    }

    // Generic convenience wrapper used only by the scaffold unit tests; the
    // daemon's Linux op path calls `start_boxed` directly.
    #[cfg(not(target_os = "linux"))]
    pub fn start<P>(
        &self,
        request: StartCommandSession,
        policy: P,
        kind: WorkspaceRunKind,
    ) -> Result<CommandResponse, CommandSessionError>
    where
        P: CommandWorkspacePolicy + 'static,
    {
        self.start_boxed(request, Box::new(policy), kind)
    }

    pub fn start_boxed(
        &self,
        request: StartCommandSession,
        policy: DynCommandWorkspacePolicy,
        kind: WorkspaceRunKind,
    ) -> Result<CommandResponse, CommandSessionError> {
        #[cfg(target_os = "linux")]
        {
            self.start_boxed_linux(request, policy, kind)
        }
        #[cfg(not(target_os = "linux"))]
        {
            self.start_boxed_scaffold(request, policy, kind)
        }
    }

    #[cfg(not(target_os = "linux"))]
    fn start_boxed_scaffold(
        &self,
        request: StartCommandSession,
        policy: DynCommandWorkspacePolicy,
        kind: WorkspaceRunKind,
    ) -> Result<CommandResponse, CommandSessionError> {
        if request.cmd.trim().is_empty() {
            return Err(CommandSessionError::InvalidRequest(
                "cmd must be non-empty".to_owned(),
            ));
        }
        let id = self.registry.next_id();
        let _prepared = policy.prepare_command_workspace(request.prepare_request(id.clone()))?;
        let caller_id = request.caller_id;
        let command = request.cmd;
        policy.command_session_started(&id, &caller_id);
        let session = CommandSession::new(CommandSessionSpec {
            id: id.clone(),
            caller_id,
            command,
            timeout_seconds: request.timeout_seconds,
        });
        self.registry
            .insert(Arc::new(RunSession::new(session, Arc::from(policy))), kind);
        Ok(CommandResponse::running(id, String::new()))
    }

    #[cfg(target_os = "linux")]
    fn start_boxed_linux(
        &self,
        request: StartCommandSession,
        policy: DynCommandWorkspacePolicy,
        kind: WorkspaceRunKind,
    ) -> Result<CommandResponse, CommandSessionError> {
        if request.cmd.trim().is_empty() {
            return Err(CommandSessionError::InvalidRequest(
                "cmd must be non-empty".to_owned(),
            ));
        }
        let id = self.registry.next_id();
        let prepared = policy.prepare_command_workspace(request.prepare_request(id.clone()))?;
        let process = spawn_current_exe_ns_runner(
            &prepared.request_path,
            &prepared.run_request,
            &prepared.output_path,
            prepared.transcript_path.clone(),
            &self.config.transcript_timestamp_timezone,
        )?;
        let caller_id = request.caller_id;
        let command = request.cmd;
        policy.command_session_started(&id, &caller_id);
        let session = CommandSession::new_running(
            CommandSessionSpec {
                id: id.clone(),
                caller_id,
                command,
                timeout_seconds: request.timeout_seconds,
            },
            RunningCommandSessionParts {
                process,
                output_path: prepared.output_path,
                final_path: prepared.final_path,
                transcript_path: prepared.transcript_path,
                output_drain_grace_ms: self.config.output_drain_grace_ms,
            },
        );
        let run = Arc::new(RunSession::new(session, Arc::from(policy)));
        self.registry.insert(Arc::clone(&run), kind);
        match wait_for_yield(&run.session, &self.config, request.yield_time_ms, 0) {
            WaitOutcome::Completed(reaped) => Ok(self.finish_reaped(run, reaped, false)),
            WaitOutcome::Running(stdout) => Ok(CommandResponse::running(id, stdout)),
        }
    }

    pub fn write_stdin(&self, request: WriteStdin) -> Result<CommandResponse, CommandSessionError> {
        #[cfg(target_os = "linux")]
        {
            self.write_stdin_linux(request)
        }
        #[cfg(not(target_os = "linux"))]
        {
            self.write_stdin_scaffold(request)
        }
    }

    #[cfg(not(target_os = "linux"))]
    fn write_stdin_scaffold(
        &self,
        request: WriteStdin,
    ) -> Result<CommandResponse, CommandSessionError> {
        if is_teardown_control(&request.chars) {
            return self.cancel(CancelCommandSession {
                command_session_id: request.command_session_id,
            });
        }
        if contains_teardown_control(&request.chars) {
            return Err(CommandSessionError::InvalidRequest(
                "Ctrl-C/Ctrl-D must be sent alone to cancel command session".to_owned(),
            ));
        }
        let Some(run) = self.registry.get(&request.command_session_id) else {
            return Err(CommandSessionError::NotFound(request.command_session_id));
        };
        if request.chars.is_empty() {
            return Err(CommandSessionError::InvalidRequest(
                "chars must be non-empty".to_owned(),
            ));
        }
        let _ = (run, request.yield_time_ms);
        Ok(CommandResponse::running(
            request.command_session_id,
            String::new(),
        ))
    }

    #[cfg(target_os = "linux")]
    fn write_stdin_linux(
        &self,
        request: WriteStdin,
    ) -> Result<CommandResponse, CommandSessionError> {
        if is_teardown_control(&request.chars) {
            return self.cancel(CancelCommandSession {
                command_session_id: request.command_session_id,
            });
        }
        if contains_teardown_control(&request.chars) {
            return Err(CommandSessionError::InvalidRequest(
                "Ctrl-C/Ctrl-D must be sent alone to cancel command session".to_owned(),
            ));
        }
        let Some(run) = self.registry.get(&request.command_session_id) else {
            return Err(CommandSessionError::NotFound(request.command_session_id));
        };
        if request.chars.is_empty() {
            return Err(CommandSessionError::InvalidRequest(
                "chars must be non-empty".to_owned(),
            ));
        }
        let command_session_id = request.command_session_id.clone();
        let start_offset = run.session.transcript_len();
        run.session.write_process_stdin(&request.chars)?;
        match wait_for_yield(
            &run.session,
            &self.config,
            request.yield_time_ms,
            start_offset,
        ) {
            WaitOutcome::Completed(reaped) => Ok(self.finish_reaped(run, reaped, false)),
            WaitOutcome::Running(stdout) => {
                Ok(CommandResponse::running(command_session_id, stdout))
            }
        }
    }

    pub fn read_progress(
        &self,
        request: ReadCommandProgress,
    ) -> Result<CommandResponse, CommandSessionError> {
        if request.last_n_lines == 0 {
            return Err(CommandSessionError::InvalidRequest(
                "last_n_lines must be >= 1".to_owned(),
            ));
        }
        let Some(run) = self.registry.get(&request.command_session_id) else {
            return self
                .registry
                .completed_result(&request.command_session_id)
                .map(|result| result.with_last_lines(request.last_n_lines))
                .ok_or(CommandSessionError::NotFound(request.command_session_id));
        };
        #[cfg(target_os = "linux")]
        if let Some(reaped) = run.session.reap() {
            return Ok(self
                .finish_reaped(run, reaped, false)
                .with_last_lines(request.last_n_lines));
        }
        Ok(CommandResponse::running(
            request.command_session_id,
            run.session.read_recent_output(request.last_n_lines),
        ))
    }

    pub fn cancel(
        &self,
        request: CancelCommandSession,
    ) -> Result<CommandResponse, CommandSessionError> {
        #[cfg(target_os = "linux")]
        {
            self.cancel_linux(request)
        }
        #[cfg(not(target_os = "linux"))]
        {
            self.cancel_scaffold(request)
        }
    }

    #[cfg(not(target_os = "linux"))]
    fn cancel_scaffold(
        &self,
        request: CancelCommandSession,
    ) -> Result<CommandResponse, CommandSessionError> {
        let Some(run) = self.registry.get(&request.command_session_id) else {
            return self
                .registry
                .take_completed_result(&request.command_session_id)
                .ok_or(CommandSessionError::NotFound(request.command_session_id));
        };
        Ok(self.finish_cancelled_scaffold(run))
    }

    #[cfg(target_os = "linux")]
    fn cancel_linux(
        &self,
        request: CancelCommandSession,
    ) -> Result<CommandResponse, CommandSessionError> {
        let Some(run) = self.registry.get(&request.command_session_id) else {
            return self
                .registry
                .take_completed_result(&request.command_session_id)
                .ok_or(CommandSessionError::NotFound(request.command_session_id));
        };
        let start_offset = run.session.transcript_len();
        run.session.cancel_process();
        match wait_for_yield(
            &run.session,
            &self.config,
            self.config.cancel_wait_ms,
            start_offset,
        ) {
            WaitOutcome::Completed(reaped) => Ok(self.finish_reaped(run, reaped, false)),
            WaitOutcome::Running(stdout) => Ok(CommandResponse::cancelled(stdout)),
        }
    }

    #[must_use]
    pub fn count_by_caller(&self, caller_id: Option<&str>) -> usize {
        self.registry.count_by_caller(caller_id)
    }

    #[must_use]
    pub fn collect_completed(&self, request: &CollectCompleted) -> CollectCompletedResponse {
        self.registry.collect_completed(request)
    }

    pub fn push_completed(&self, completion: CommandSessionCompletion) {
        self.registry.push_completed(completion);
    }

    /// Cancel and discard every command session owned by `caller_id` (the
    /// per-caller workspace-run teardown). Cancelled sessions discard their
    /// overlay and push no completion (the caller initiated the cancel).
    #[must_use]
    pub fn cleanup_caller(&self, caller_id: &str, grace_s: Option<f64>) -> usize {
        #[cfg(target_os = "linux")]
        {
            let caller_id = caller_id.trim();
            if caller_id.is_empty() {
                return 0;
            }
            self.cancel_and_drain(self.registry.caller_sessions(caller_id), grace_s)
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (caller_id, grace_s);
            0
        }
    }

    /// Cancel and discard every live command session in the sandbox (the
    /// whole-sandbox sweep backstop). Like `cleanup_caller` but across all
    /// callers; cancelled sessions discard and push no completion.
    #[must_use]
    pub fn cancel_all(&self, grace_s: Option<f64>) -> usize {
        #[cfg(target_os = "linux")]
        {
            self.cancel_and_drain(self.registry.live(), grace_s)
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = grace_s;
            0
        }
    }

    /// Cancel every run, then reap+discard within `grace`, finalizing any
    /// stragglers. Returns the number of runs that were live at entry.
    #[cfg(target_os = "linux")]
    fn cancel_and_drain(&self, runs: Vec<Arc<RunSession>>, grace_s: Option<f64>) -> usize {
        if runs.is_empty() {
            return 0;
        }
        for run in &runs {
            run.session.cancel_process();
        }

        let cancel_wait_s = self.config.cancel_wait_ms as f64 / 1000.0;
        let wait_s = grace_s.unwrap_or(cancel_wait_s).max(cancel_wait_s);
        let deadline = Instant::now() + Duration::from_secs_f64(wait_s);
        let mut pending = runs.clone();
        loop {
            pending.retain(|run| match run.session.reap() {
                Some(reaped) => {
                    let _ = self.finish_reaped(Arc::clone(run), reaped, false);
                    false
                }
                None => true,
            });
            if pending.is_empty() || Instant::now() >= deadline {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        for run in pending {
            if let Some(reaped) = run.session.reap() {
                let _ = self.finish_reaped(run, reaped, false);
            }
        }
        runs.len()
    }

    #[must_use]
    pub fn sweep_expired(&self, now: Instant) -> SweepReport {
        #[cfg(target_os = "linux")]
        {
            self.sweep_linux(now)
        }
        #[cfg(not(target_os = "linux"))]
        {
            self.sweep_scaffold(now)
        }
    }

    #[cfg(not(target_os = "linux"))]
    fn sweep_scaffold(&self, now: Instant) -> SweepReport {
        let mut expired = 0;
        for run in self.registry.live() {
            if run.session.is_expired(now) && self.registry.remove(run.session.id()).is_some() {
                expired += 1;
            }
        }
        SweepReport {
            expired,
            live: self.registry.live().len(),
        }
    }

    #[cfg(target_os = "linux")]
    fn sweep_linux(&self, now: Instant) -> SweepReport {
        let mut expired = 0;
        for run in self.registry.live() {
            if run.session.is_past_deadline(now, self.config.max_session_s) {
                expired += 1;
                run.session.cancel_process();
            }
            if let Some(reaped) = run.session.reap() {
                let publish_completion = !reaped.cancelled;
                let _ = self.finish_reaped(run, reaped, publish_completion);
            }
        }
        SweepReport {
            expired,
            live: self.registry.live().len(),
        }
    }

    /// Turn a reaped command into its final response: the run publishes (normal
    /// completion) or discards (cancel) via its policy, then persists the final
    /// response. Routing cancel to `discard_command_workspace` is the structural
    /// guarantee that a cancelled command never reaches the OCC merge.
    #[cfg(target_os = "linux")]
    fn finish_reaped(
        &self,
        run: Arc<RunSession>,
        reaped: ReapedCommand,
        publish_completion: bool,
    ) -> CommandResponse {
        let request = FinalizeCommandRequest {
            runner_result: reaped.runner_result,
            command_elapsed_s: reaped.elapsed_s,
            status: reaped.status,
            exit_code: Some(reaped.exit_code),
            stdout: reaped.stdout,
            stderr: String::new(),
            command_session_id: Some(run.session.id().to_owned()),
        };
        self.finalize_run(run, request, reaped.cancelled, publish_completion)
    }

    /// The non-Linux scaffold has no real process; its only settle path is
    /// cancel, which discards (never publishes) and parks a completion.
    #[cfg(not(target_os = "linux"))]
    fn finish_cancelled_scaffold(&self, run: Arc<RunSession>) -> CommandResponse {
        let request = FinalizeCommandRequest {
            runner_result: None,
            command_elapsed_s: run.session.elapsed_s(),
            status: "cancelled".to_owned(),
            exit_code: Some(130),
            stdout: String::new(),
            stderr: String::new(),
            command_session_id: Some(run.session.id().to_owned()),
        };
        self.finalize_run(run, request, true, true)
    }

    /// Apply the run's workspace policy to `request` (publish on complete,
    /// discard on cancel), persist the final response, fire the finished hook,
    /// remove the run from the registry, and park a completion if requested.
    fn finalize_run(
        &self,
        run: Arc<RunSession>,
        request: FinalizeCommandRequest,
        cancelled: bool,
        publish_completion: bool,
    ) -> CommandResponse {
        let outcome = if cancelled {
            run.policy.discard_command_workspace(request)
        } else {
            run.policy.finalize_command_workspace(request)
        };
        let response = match outcome {
            Ok(outcome) => CommandResponse::from_workspace_outcome(outcome),
            Err(error) => CommandResponse::error(error.to_string()),
        };
        #[cfg(target_os = "linux")]
        run.session.persist_final(&response);
        run.policy.command_session_finished(
            run.session.id(),
            run.session.caller_id(),
            &response.status,
        );
        let command_session_id = run.session.id().to_owned();
        let caller_id = run.session.caller_id().to_owned();
        let command = run.session.command().to_owned();
        self.registry.remove(&command_session_id);
        if publish_completion {
            let notification_result = response.clone();
            self.registry.push_completed(CommandSessionCompletion {
                command_session_id,
                caller_id,
                command,
                result: response.clone(),
                notification_result,
            });
        }
        response
    }
}

fn is_teardown_control(chars: &str) -> bool {
    matches!(chars, "\u{3}" | "\u{4}")
}

fn contains_teardown_control(chars: &str) -> bool {
    chars.contains('\u{3}') || chars.contains('\u{4}')
}

impl Default for WorkspaceRunManager {
    fn default() -> Self {
        Self::new(CommandSessionConfig::default())
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct SweepReport {
    pub expired: usize,
    pub live: usize,
}

// The manager unit tests drive the non-Linux scaffold (no real PTY); on Linux
// the same behavior is proven by the daemon E2E suite.
#[cfg(all(test, not(target_os = "linux")))]
#[path = "../../../tests/workspace_run/manager_fake_policy.rs"]
mod manager_fake_policy;

#[cfg(all(test, not(target_os = "linux")))]
mod tests {
    use std::path::PathBuf;
    use std::sync::Arc;
    use std::time::{Duration, Instant};

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
                run_request: json!({ "cmd": request.cmd }),
                request_path: session_dir.join("runner-request.json"),
                output_path: session_dir.join("runner-result.json"),
                final_path: session_dir.join("final.json"),
                session_dir: session_dir.clone(),
                transcript_path: session_dir.join("transcript.log"),
            })
        }

        fn finalize_command_workspace(
            &self,
            request: FinalizeCommandRequest,
        ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError> {
            Ok(WorkspaceCommandOutcome {
                mode: WorkspaceMode::default(),
                success: request.command_succeeded(),
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

        fn discard_command_workspace(
            &self,
            request: FinalizeCommandRequest,
        ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError> {
            Ok(WorkspaceCommandOutcome::discarded(
                WorkspaceMode::default(),
                request,
            ))
        }
    }

    #[test]
    fn manager_registers_counts_and_sweeps_sessions() {
        let manager = WorkspaceRunManager::default();
        let started = manager
            .start(
                StartCommandSession {
                    invocation_id: "inv".to_owned(),
                    caller_id: "caller".to_owned(),
                    cmd: "sleep 1".to_owned(),
                    timeout_seconds: Some(0.001),
                    yield_time_ms: 1000,
                },
                ExpiringPolicy,
                WorkspaceRunKind::Ephemeral,
            )
            .unwrap_or_else(|error| panic!("start session: {error}"));
        let id = started
            .command_session_id
            .unwrap_or_else(|| panic!("running session id"));
        assert!(id.starts_with("cmd_"));

        assert_eq!(manager.count_by_caller(Some("caller")), 1);

        let report = manager.sweep_expired(Instant::now() + Duration::from_millis(2));

        assert_eq!(report.expired, 1);
        assert_eq!(report.live, 0);
    }

    fn ephemeral_request(caller_id: &str) -> StartCommandSession {
        StartCommandSession {
            invocation_id: "inv".to_owned(),
            caller_id: caller_id.to_owned(),
            cmd: "sleep 1".to_owned(),
            timeout_seconds: None,
            yield_time_ms: 1000,
        }
    }

    #[test]
    fn caller_may_hold_multiple_sessions_per_kind() {
        // A caller holds many ephemeral command sessions (each its own ephemeral
        // workspace); an isolated caller holds many sessions in its one workspace.
        for kind in [WorkspaceRunKind::Ephemeral, WorkspaceRunKind::Isolated] {
            let manager = WorkspaceRunManager::default();
            for _ in 0..3 {
                manager
                    .start(ephemeral_request("caller"), ExpiringPolicy, kind)
                    .unwrap_or_else(|error| panic!("start ({kind:?}): {error}"));
            }
            assert_eq!(manager.count_by_caller(Some("caller")), 3);
            assert_eq!(manager.count_by_caller(Some("other")), 0);
        }
    }

    #[test]
    fn collected_completion_preserves_finalized_stdout() {
        let manager = WorkspaceRunManager::default();
        let command_session_id = "cmd_full".to_owned();
        let session = CommandSession::new(CommandSessionSpec {
            id: command_session_id.clone(),
            caller_id: "caller".to_owned(),
            command: "printf full".to_owned(),
            timeout_seconds: None,
        });
        let run = Arc::new(RunSession::new(session, Arc::new(ExpiringPolicy)));
        let request = FinalizeCommandRequest {
            runner_result: None,
            command_elapsed_s: 0.0,
            status: "ok".to_owned(),
            exit_code: Some(0),
            stdout: "full transcript stdout".to_owned(),
            stderr: String::new(),
            command_session_id: Some(command_session_id.clone()),
        };

        let returned = manager.finalize_run(run, request, false, true);

        assert_eq!(returned.stdout, "full transcript stdout");
        let completions = manager.collect_completed(&CollectCompleted {
            command_session_ids: Some(vec![command_session_id]),
            caller_id: Some("caller".to_owned()),
        });
        assert_eq!(completions.completions.len(), 1);
        assert_eq!(
            completions.completions[0].result.stdout,
            "full transcript stdout"
        );
        assert_eq!(
            completions.completions[0].notification_result.stdout,
            "full transcript stdout"
        );
    }
}
