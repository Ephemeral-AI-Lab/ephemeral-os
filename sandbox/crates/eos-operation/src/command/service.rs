use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use eos_command::process::{
    CommandFinalResponsePersistence, CommandPersistenceOutcome, CommandProcess, CommandProcessExit,
    CommandProcessSpawn, CommandProcessSpec, KillReason,
};
use eos_command::yield_wait_loop::{wait_for_yield, WaitOutcome};
use eos_command::{
    CancelCommand, CollectCompleted, CommandConfig, CommandError, ReadCommandProgress,
    StartCommand, WriteStdin,
};
use eos_layerstack::service;
use eos_trace::{
    EventRecord, RequestId, SpanKind, SpanRecord, SpanStatus, SpanUid, TraceId, TraceKind,
    TraceLink, TraceLinkKind, TraceRecord,
};
use eos_workspace::EphemeralWorkspace;
use eos_workspace::IsolatedWorkspaceBinding;
use serde_json::json;

use crate::WorkspaceKind;

use super::contract::{CollectCompletedOutput, CommandCompletion, CommandResponse, CommandStatus};
use super::finalize::{discarded_response, finalize_ephemeral_command, finalize_isolated_command};
use super::outcome::FinalizeCommandRequest;
use super::prepare::{prepare_ephemeral, prepare_isolated, PrepareInputs, PreparedCommand};
use super::registry::{
    ActiveCommand, CommandRegistry, CommandTraceOrigin, CompletionBufferEviction, EphemeralRun,
    IsolatedRun,
};
use super::trace::CommandTraceEvent;

pub enum ExecTarget {
    Ephemeral {
        root: PathBuf,
        workspace_root: PathBuf,
        scratch_root: PathBuf,
    },
    Isolated {
        binding: Box<IsolatedWorkspaceBinding>,
    },
}

pub struct CommandOps {
    config: CommandConfig,
    registry: Arc<CommandRegistry>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandStdinTraceFacts {
    pub command_id: String,
    pub bytes: usize,
    pub wait_ms: u64,
    pub waited_for_output: bool,
    pub status: CommandStatus,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommandWriteStdinOutcome {
    pub response: CommandResponse,
    pub trace: Option<CommandStdinTraceFacts>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommandExecOutcome {
    pub response: CommandResponse,
    pub trace_events: Vec<CommandTraceEvent>,
}

impl CommandOps {
    #[must_use]
    pub fn new(config: CommandConfig) -> Self {
        Self {
            config,
            registry: Arc::new(CommandRegistry::new()),
        }
    }

    pub fn exec_command(
        &self,
        request: StartCommand,
        target: ExecTarget,
    ) -> Result<CommandResponse, CommandError> {
        self.exec_command_with_trace(request, target)
            .map(|outcome| outcome.response)
    }

    pub fn exec_command_with_trace(
        &self,
        request: StartCommand,
        target: ExecTarget,
    ) -> Result<CommandExecOutcome, CommandError> {
        if request.cmd.trim().is_empty() {
            return Err(CommandError::InvalidRequest(
                "cmd must be non-empty".to_owned(),
            ));
        }
        let id = self.registry.next_id();
        let yield_time_ms = request.yield_time_ms;
        let spec = CommandProcessSpec {
            id: id.clone(),
            caller_id: request.caller_id.clone(),
            command: request.cmd.clone(),
            timeout_seconds: request.timeout_seconds,
        };
        match target {
            ExecTarget::Ephemeral {
                root,
                workspace_root,
                scratch_root,
            } => self.start_ephemeral(
                spec,
                &request,
                &id,
                root,
                workspace_root,
                scratch_root,
                yield_time_ms,
            ),
            ExecTarget::Isolated { binding } => {
                self.start_isolated(spec, &request, &id, binding, yield_time_ms)
            }
        }
    }

    #[expect(
        clippy::too_many_arguments,
        reason = "start inputs are one-shot plumbing from the typed target"
    )]
    fn start_ephemeral(
        &self,
        spec: CommandProcessSpec,
        request: &StartCommand,
        command_id: &str,
        root: PathBuf,
        workspace_root: PathBuf,
        scratch_root: PathBuf,
        yield_time_ms: u64,
    ) -> Result<CommandExecOutcome, CommandError> {
        let request_id = format!("command:{}:{}", request.caller_id, request.invocation_id);
        let snapshot = service::acquire_snapshot(&root, &request_id)
            .map_err(|error| CommandError::Workspace(error.to_string()))?;
        let writable_root = eos_overlay::overlay_writable_root()
            .map_err(|error| CommandError::Workspace(error.to_string()));
        let result = writable_root.and_then(|writable_root| {
            let workspace = EphemeralWorkspace::create(
                &writable_root.join("runtime"),
                "sandbox-overlay",
                &request.invocation_id,
            )
            .map_err(|error| CommandError::Workspace(error.to_string()))?;
            let prepared = prepare_ephemeral(
                PrepareInputs {
                    caller_id: &request.caller_id,
                    command_id,
                    invocation_id: &request.invocation_id,
                    cmd: &request.cmd,
                    timeout_seconds: request.timeout_seconds,
                    command_dir: scratch_root.join(command_id),
                    workspace_label: "ephemeral",
                },
                &workspace_root,
                &snapshot.layer_paths,
                workspace.dirs(),
                &workspace.dirs().run_dir,
            )?;
            let mut trace_events = prepared.trace_events.clone();
            let process = self.spawn_process(spec, prepared, &mut trace_events)?;
            Ok((workspace, process, trace_events))
        });
        let (workspace, process, trace_events) = match result {
            Ok(parts) => parts,
            Err(error) => {
                let _ = service::release_lease(&root, &snapshot.lease_id);
                return Err(error);
            }
        };
        let trace_origin = CommandTraceOrigin::from_start(request);
        Ok(self.register_and_wait(
            process,
            yield_time_ms,
            move |process| {
                ActiveCommand::Ephemeral(EphemeralRun {
                    process,
                    trace_origin,
                    root,
                    snapshot,
                    workspace,
                })
            },
            trace_events,
        ))
    }

    fn start_isolated(
        &self,
        spec: CommandProcessSpec,
        request: &StartCommand,
        command_id: &str,
        binding: Box<IsolatedWorkspaceBinding>,
        yield_time_ms: u64,
    ) -> Result<CommandExecOutcome, CommandError> {
        let prepared = prepare_isolated(
            PrepareInputs {
                caller_id: &request.caller_id,
                command_id,
                invocation_id: &request.invocation_id,
                cmd: &request.cmd,
                timeout_seconds: request.timeout_seconds,
                command_dir: binding.scratch_dir.join("commands").join(command_id),
                workspace_label: "isolated",
            },
            &binding,
        )?;
        let mut trace_events = prepared.trace_events.clone();
        let process = self.spawn_process(spec, prepared, &mut trace_events)?;
        let binding = *binding;
        let trace_origin = CommandTraceOrigin::from_start(request);
        Ok(self.register_and_wait(
            process,
            yield_time_ms,
            move |process| {
                ActiveCommand::Isolated(IsolatedRun {
                    process,
                    trace_origin,
                    binding,
                })
            },
            trace_events,
        ))
    }

    fn spawn_process(
        &self,
        spec: CommandProcessSpec,
        prepared: PreparedCommand,
        trace_events: &mut Vec<CommandTraceEvent>,
    ) -> Result<CommandProcess, CommandError> {
        let command_id = spec.id.clone();
        let request_path = prepared.request_path.clone();
        let process = CommandProcess::spawn(
            spec,
            CommandProcessSpawn {
                run_request: prepared.run_request,
                request_path: prepared.request_path,
                output_path: prepared.output_path,
                final_path: prepared.final_path,
                transcript_path: prepared.transcript_path,
                transcript_timestamp_timezone: &self.config.transcript_timestamp_timezone,
                output_drain_grace_ms: self.config.output_drain_grace_ms,
            },
        )?;
        let request_bytes = std::fs::metadata(&request_path).map_or(0, |metadata| {
            usize::try_from(metadata.len()).unwrap_or(usize::MAX)
        });
        trace_events.push(CommandTraceEvent::artifact_written(
            "runner_request",
            &request_path,
            request_bytes,
        ));
        trace_events.push(CommandTraceEvent::new(
            "spawned",
            json!({
                "command_id": command_id,
            }),
        ));
        Ok(process)
    }

    fn register_and_wait(
        &self,
        process: CommandProcess,
        yield_time_ms: u64,
        make_run: impl FnOnce(CommandProcess) -> ActiveCommand,
        mut trace_events: Vec<CommandTraceEvent>,
    ) -> CommandExecOutcome {
        let id = process.id().to_owned();
        let run = Arc::new(make_run(process));
        self.registry.insert(Arc::clone(&run));
        let response = self.wait_on_run(run, yield_time_ms, 0, |stdout| {
            CommandResponse::running(id, stdout)
        });
        trace_events.push(CommandTraceEvent::new(
            "yielded",
            json!({
                "command_id": response.command_id.as_ref().map(ToString::to_string),
                "status": response.status.as_str(),
            }),
        ));
        CommandExecOutcome {
            response,
            trace_events,
        }
    }

    fn wait_on_run(
        &self,
        run: Arc<ActiveCommand>,
        wait_ms: u64,
        start_offset: u64,
        on_running: impl FnOnce(String) -> CommandResponse,
    ) -> CommandResponse {
        match wait_for_yield(run.process(), &self.config, wait_ms, start_offset) {
            WaitOutcome::Completed(process_exit) => self.finalize_command(run, process_exit, false),
            WaitOutcome::Running(stdout) => on_running(stdout),
        }
    }

    pub fn write_stdin(&self, request: WriteStdin) -> Result<CommandResponse, CommandError> {
        self.write_stdin_with_trace(request)
            .map(|outcome| outcome.response)
    }

    pub fn write_stdin_with_trace(
        &self,
        request: WriteStdin,
    ) -> Result<CommandWriteStdinOutcome, CommandError> {
        if is_teardown_control(&request.chars) {
            let response = self.cancel(CancelCommand {
                command_id: request.command_id,
            })?;
            return Ok(CommandWriteStdinOutcome {
                response,
                trace: None,
            });
        }
        if contains_teardown_control(&request.chars) {
            return Err(CommandError::InvalidRequest(
                "Ctrl-C/Ctrl-D must be sent alone to cancel command process".to_owned(),
            ));
        }
        let Some(run) = self.registry.get(&request.command_id) else {
            return Err(CommandError::NotFound(request.command_id));
        };
        if request.chars.is_empty() {
            return Err(CommandError::InvalidRequest(
                "chars must be non-empty".to_owned(),
            ));
        }
        let bytes = request.chars.len();
        let waited_for_output = request.yield_time_ms > 0;
        let command_id = request.command_id.clone();
        let start_offset = run.process().transcript_len();
        let wait_started = Instant::now();
        run.process().write_process_stdin(&request.chars)?;
        let response = self.wait_on_run(run, request.yield_time_ms, start_offset, |stdout| {
            CommandResponse::running(command_id.clone(), stdout)
        });
        let status = response.status;
        Ok(CommandWriteStdinOutcome {
            response,
            trace: Some(CommandStdinTraceFacts {
                command_id,
                bytes,
                wait_ms: elapsed_ms(wait_started),
                waited_for_output,
                status,
            }),
        })
    }

    pub fn read_command_progress(
        &self,
        request: ReadCommandProgress,
    ) -> Result<CommandResponse, CommandError> {
        if request.last_n_lines == 0 {
            return Err(CommandError::InvalidRequest(
                "last_n_lines must be >= 1".to_owned(),
            ));
        }
        let Some(run) = self.registry.get(&request.command_id) else {
            return self
                .registry
                .completed_result(&request.command_id)
                .map(|result| result.with_last_lines(request.last_n_lines))
                .ok_or(CommandError::NotFound(request.command_id));
        };
        if let Some(process_exit) = run.process().take_exit() {
            return Ok(self
                .finalize_command(run, process_exit, false)
                .with_last_lines(request.last_n_lines));
        }
        Ok(CommandResponse::running(
            request.command_id,
            run.process().read_recent_output(request.last_n_lines),
        ))
    }

    pub fn cancel(&self, request: CancelCommand) -> Result<CommandResponse, CommandError> {
        let Some(run) = self.registry.get(&request.command_id) else {
            return self
                .registry
                .take_completed_result(&request.command_id)
                .ok_or(CommandError::NotFound(request.command_id));
        };
        let start_offset = run.process().transcript_len();
        run.process().cancel_process();
        Ok(
            self.wait_on_run(run, self.config.cancel_wait_ms, start_offset, |stdout| {
                CommandResponse::cancelled(stdout)
            }),
        )
    }

    #[must_use]
    pub fn count_by_caller(&self, caller_id: Option<&str>) -> usize {
        self.registry.count_by_caller(caller_id)
    }

    #[must_use]
    pub fn collect_completed(&self, request: &CollectCompleted) -> CollectCompletedOutput {
        self.registry.collect_completed(request)
    }

    pub fn push_completed(&self, completion: CommandCompletion) {
        let _ = self.registry.push_completed(completion);
    }

    #[must_use]
    pub fn cleanup_caller(&self, caller_id: &str, grace_s: Option<f64>) -> usize {
        let caller_id = caller_id.trim();
        if caller_id.is_empty() {
            return 0;
        }
        self.cancel_and_drain(self.registry.caller_commands(caller_id), grace_s)
    }

    #[must_use]
    pub fn cancel_all(&self, grace_s: Option<f64>) -> usize {
        self.cancel_and_drain(self.registry.live(), grace_s)
    }

    fn cancel_and_drain(&self, runs: Vec<Arc<ActiveCommand>>, grace_s: Option<f64>) -> usize {
        if runs.is_empty() {
            return 0;
        }
        for run in &runs {
            run.process().cancel_process();
        }
        let cancel_wait_s = self.config.cancel_wait_ms as f64 / 1000.0;
        let wait_s = grace_s.unwrap_or(cancel_wait_s).max(cancel_wait_s);
        let deadline = Instant::now() + Duration::from_secs_f64(wait_s);
        let mut pending = runs.clone();
        loop {
            pending.retain(|run| match run.process().take_exit() {
                Some(process_exit) => {
                    let _ = self.finalize_command(Arc::clone(run), process_exit, false);
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
            if let Some(process_exit) = run.process().take_exit() {
                let _ = self.finalize_command(run, process_exit, false);
            } else {
                self.force_discard(&run);
            }
        }
        runs.len()
    }

    fn force_discard(&self, run: &Arc<ActiveCommand>) {
        if self.registry.remove(run.process().id()).is_none() {
            return;
        }
        if let ActiveCommand::Ephemeral(ephemeral) = &**run {
            let _ = service::release_lease(&ephemeral.root, &ephemeral.snapshot.lease_id);
        }
    }

    pub fn advance_active_commands_once(&self, now: Instant) -> Vec<TraceRecord> {
        let mut records = Vec::new();
        for run in self.registry.live() {
            if run
                .process()
                .is_past_deadline(now, self.config.max_command_s)
            {
                run.process().time_out_process();
            }
            if let Some(process_exit) = run.process().take_exit() {
                let publish_completion = process_exit.kill != Some(KillReason::Cancelled);
                let finalized = self.finalize_command_inner(run, process_exit, publish_completion);
                records.push(command_finalize_trace_record(&finalized.trace));
            }
        }
        records
    }

    fn finalize_command(
        &self,
        run: Arc<ActiveCommand>,
        process_exit: CommandProcessExit,
        publish_completion: bool,
    ) -> CommandResponse {
        self.finalize_command_inner(run, process_exit, publish_completion)
            .response
    }

    fn finalize_command_inner(
        &self,
        run: Arc<ActiveCommand>,
        process_exit: CommandProcessExit,
        publish_completion: bool,
    ) -> FinalizedCommand {
        let trace_origin = run.trace_origin().clone();
        let command_id = run.process().id().to_owned();
        let caller_id = run.process().caller_id().to_owned();
        let command = run.process().command().to_owned();
        let exit_code = process_exit.exit_code;
        let signal = process_exit.signal;
        let command_elapsed_s = process_exit.elapsed_s;
        let kill = process_exit.kill;
        let request = FinalizeCommandRequest {
            runner_result: process_exit.runner_result,
            command_elapsed_s,
            status: CommandStatus::from_wire_str(&process_exit.status)
                .unwrap_or(CommandStatus::Error),
            exit_code: Some(exit_code),
            stdout: process_exit.stdout,
            stderr: String::new(),
            command_id: Some(command_id.clone()),
        };
        let cancelled = kill.is_some();
        let outcome = match &*run {
            ActiveCommand::Ephemeral(ephemeral) => {
                let outcome = if cancelled {
                    Ok(discarded_response(WorkspaceKind::Ephemeral, request))
                } else {
                    finalize_ephemeral_command(
                        &ephemeral.root,
                        &ephemeral.snapshot,
                        &ephemeral.workspace,
                        request,
                    )
                };
                let _ = service::release_lease(&ephemeral.root, &ephemeral.snapshot.lease_id);
                outcome
            }
            ActiveCommand::Isolated(isolated) => {
                if cancelled {
                    Ok(discarded_response(WorkspaceKind::Isolated, request))
                } else {
                    finalize_isolated_command(&isolated.binding, request)
                }
            }
        };
        let response = match outcome {
            Ok(response) => response,
            Err(error) => CommandResponse::error(error.to_string()),
        };
        let persistence = run.process().persist_final(&response.to_wire_value());
        self.registry.remove(&command_id);
        let trace_command_id = command_id.clone();
        let trace_caller_id = caller_id.clone();
        let mut evictions = Vec::new();
        if publish_completion {
            evictions = self.registry.push_completed(CommandCompletion {
                command_id,
                caller_id,
                command,
                result: response.clone(),
            });
        }
        FinalizedCommand {
            trace: CommandFinalizeTraceFacts {
                trace_origin,
                command_id: trace_command_id,
                caller_id: trace_caller_id,
                status: response.status,
                exit_code: response.exit_code,
                signal,
                kill,
                command_elapsed_s,
                persistence,
                publish_completion,
                evictions,
            },
            response,
        }
    }
}

struct FinalizedCommand {
    response: CommandResponse,
    trace: CommandFinalizeTraceFacts,
}

struct CommandFinalizeTraceFacts {
    trace_origin: CommandTraceOrigin,
    command_id: String,
    caller_id: String,
    status: CommandStatus,
    exit_code: Option<i64>,
    signal: Option<i32>,
    kill: Option<KillReason>,
    command_elapsed_s: f64,
    persistence: CommandPersistenceOutcome,
    publish_completion: bool,
    evictions: Vec<CompletionBufferEviction>,
}

fn command_finalize_trace_record(facts: &CommandFinalizeTraceFacts) -> TraceRecord {
    let now = unix_now_ms();
    let mut span = SpanRecord::new(
        SpanUid::ROOT,
        None,
        "command.finalize",
        SpanKind::CommandFinalize,
        json!({
            "command_id": facts.command_id,
            "caller_id": facts.caller_id,
            "origin_request_id": facts.trace_origin.request_id,
            "publish_completion": facts.publish_completion,
        }),
    );
    span.started_at_unix_ms = now;
    span.finished_at_unix_ms = now;
    span.status = Some(command_span_status(facts.status));

    let mut events = vec![
        EventRecord::new(
            SpanUid::ROOT,
            "exit_taken",
            "command",
            json!({
                "command_id": facts.command_id,
                "exit_code": facts.exit_code,
                "signal": facts.signal,
                "kill_reason": facts.kill.map(kill_reason_label),
            }),
        ),
        EventRecord::new(
            SpanUid::ROOT,
            "finalized",
            "command",
            json!({
                "command_id": facts.command_id,
                "caller_id": facts.caller_id,
                "status": facts.status.as_str(),
                "exit_code": facts.exit_code,
                "signal": facts.signal,
                "kill_reason": facts.kill.map(kill_reason_label),
                "elapsed_s": facts.command_elapsed_s,
                "publish_completion": facts.publish_completion,
            }),
        ),
    ];
    events.extend(facts.evictions.iter().map(|eviction| {
        EventRecord::new(
            SpanUid::ROOT,
            "completion_buffer_evicted",
            "command",
            json!({
                "command_id": eviction.command_id,
                "seq": eviction.seq,
                "max_entries": eviction.max_entries,
            }),
        )
    }));
    append_persistence_events(&mut events, &facts.persistence);
    for event in &mut events {
        event.at_unix_ms = now;
    }

    let mut record = TraceRecord::new(trace_id_from_origin(&facts.trace_origin), SpanUid::ROOT);
    record.request_id = facts
        .trace_origin
        .request_id
        .as_ref()
        .and_then(|request_id| RequestId::parse(request_id.clone()).ok());
    record.kind = TraceKind::CommandFinalize;
    record.started_at_unix_ms = now;
    record.finished_at_unix_ms = now;
    record.spans.push(span);
    record.events = events;
    record.links.push(TraceLink {
        kind: TraceLinkKind::Command,
        value: facts.command_id.clone(),
    });
    record
}

fn append_persistence_events(
    events: &mut Vec<EventRecord>,
    persistence: &CommandPersistenceOutcome,
) {
    match &persistence.final_response {
        Some(CommandFinalResponsePersistence::Persisted { path, bytes }) => {
            events.push(EventRecord::new(
                SpanUid::ROOT,
                "final_persisted",
                "command",
                json!({
                    "path": path.display().to_string(),
                    "bytes": bytes,
                }),
            ));
        }
        Some(CommandFinalResponsePersistence::Failed { path, error }) => {
            events.push(EventRecord::new(
                SpanUid::ROOT,
                "final_persist_failed",
                "command",
                json!({
                    "path": path.display().to_string(),
                    "error": error,
                }),
            ));
        }
        None => {}
    }

    if let Some(error) = &persistence.transcript_error {
        events.push(EventRecord::new(
            SpanUid::ROOT,
            "transcript_failed",
            "command",
            json!({
                "path": error.path.display().to_string(),
                "error": error.error,
            }),
        ));
    }
}

fn trace_id_from_origin(origin: &CommandTraceOrigin) -> TraceId {
    origin
        .trace_id
        .as_ref()
        .and_then(|trace_id| TraceId::parse(trace_id.clone()).ok())
        .unwrap_or_default()
}

fn command_span_status(status: CommandStatus) -> SpanStatus {
    match status {
        CommandStatus::Running | CommandStatus::Ok => SpanStatus::Ok,
        CommandStatus::Cancelled => SpanStatus::Cancelled,
        CommandStatus::Error => SpanStatus::Error,
        CommandStatus::TimedOut => SpanStatus::TimedOut,
    }
}

fn kill_reason_label(reason: KillReason) -> &'static str {
    match reason {
        KillReason::Cancelled => "cancelled",
        KillReason::TimedOut => "timed_out",
    }
}

fn unix_now_ms() -> u64 {
    let millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    u64::try_from(millis).unwrap_or(u64::MAX)
}

fn elapsed_ms(started: Instant) -> u64 {
    u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX)
}

fn is_teardown_control(chars: &str) -> bool {
    matches!(chars, "\u{3}" | "\u{4}")
}

fn contains_teardown_control(chars: &str) -> bool {
    chars.contains('\u{3}') || chars.contains('\u{4}')
}

#[cfg(test)]
#[path = "../../tests/command/service.rs"]
mod tests;
