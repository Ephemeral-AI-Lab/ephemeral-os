use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use eos_command::process::{
    CommandProcess, CommandProcessExit, CommandProcessSpawn, CommandProcessSpec, KillReason,
};
use eos_command::yield_wait_loop::{wait_for_yield, WaitOutcome};
use eos_command::{
    CancelCommand, CollectCompleted, CommandConfig, CommandError, ReadCommandProgress,
    StartCommand, WriteStdin,
};
use eos_layerstack::service;
use eos_workspace::EphemeralWorkspace;
use eos_workspace::IsolatedWorkspaceBinding;

use crate::WorkspaceKind;

use super::contract::{CollectCompletedOutput, CommandCompletion, CommandResponse, CommandStatus};
use super::finalize::{discarded_response, finalize_ephemeral_command, finalize_isolated_command};
use super::outcome::FinalizeCommandRequest;
use super::prepare::{prepare_ephemeral, prepare_isolated, PrepareInputs, PreparedCommand};
use super::registry::{
    ActiveCommand, CommandRegistry, CommandTraceOrigin, EphemeralRun, IsolatedRun,
};

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
    ) -> Result<CommandResponse, CommandError> {
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
            let process = self.spawn_process(spec, prepared)?;
            Ok((workspace, process))
        });
        let (workspace, process) = match result {
            Ok(parts) => parts,
            Err(error) => {
                let _ = service::release_lease(&root, &snapshot.lease_id);
                return Err(error);
            }
        };
        let trace_origin = CommandTraceOrigin::from_start(request);
        Ok(
            self.register_and_wait(process, yield_time_ms, move |process| {
                ActiveCommand::Ephemeral(EphemeralRun {
                    process,
                    trace_origin,
                    root,
                    snapshot,
                    workspace,
                })
            }),
        )
    }

    fn start_isolated(
        &self,
        spec: CommandProcessSpec,
        request: &StartCommand,
        command_id: &str,
        binding: Box<IsolatedWorkspaceBinding>,
        yield_time_ms: u64,
    ) -> Result<CommandResponse, CommandError> {
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
        let process = self.spawn_process(spec, prepared)?;
        let binding = *binding;
        let trace_origin = CommandTraceOrigin::from_start(request);
        Ok(
            self.register_and_wait(process, yield_time_ms, move |process| {
                ActiveCommand::Isolated(IsolatedRun {
                    process,
                    trace_origin,
                    binding,
                })
            }),
        )
    }

    fn spawn_process(
        &self,
        spec: CommandProcessSpec,
        prepared: PreparedCommand,
    ) -> Result<CommandProcess, CommandError> {
        CommandProcess::spawn(
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
        )
    }

    fn register_and_wait(
        &self,
        process: CommandProcess,
        yield_time_ms: u64,
        make_run: impl FnOnce(CommandProcess) -> ActiveCommand,
    ) -> CommandResponse {
        let id = process.id().to_owned();
        let run = Arc::new(make_run(process));
        self.registry.insert(Arc::clone(&run));
        self.wait_on_run(run, yield_time_ms, 0, |stdout| {
            CommandResponse::running(id, stdout)
        })
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
        if is_teardown_control(&request.chars) {
            return self.cancel(CancelCommand {
                command_id: request.command_id,
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
        let command_id = request.command_id.clone();
        let start_offset = run.process().transcript_len();
        run.process().write_process_stdin(&request.chars)?;
        Ok(
            self.wait_on_run(run, request.yield_time_ms, start_offset, |stdout| {
                CommandResponse::running(command_id, stdout)
            }),
        )
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
        self.registry.push_completed(completion);
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

    pub fn advance_active_commands_once(&self, now: Instant) {
        for run in self.registry.live() {
            if run
                .process()
                .is_past_deadline(now, self.config.max_command_s)
            {
                run.process().time_out_process();
            }
            if let Some(process_exit) = run.process().take_exit() {
                let publish_completion = process_exit.kill != Some(KillReason::Cancelled);
                let _ = self.finalize_command(run, process_exit, publish_completion);
            }
        }
    }

    fn finalize_command(
        &self,
        run: Arc<ActiveCommand>,
        process_exit: CommandProcessExit,
        publish_completion: bool,
    ) -> CommandResponse {
        let request = FinalizeCommandRequest {
            runner_result: process_exit.runner_result,
            command_elapsed_s: process_exit.elapsed_s,
            status: CommandStatus::from_wire_str(&process_exit.status)
                .unwrap_or(CommandStatus::Error),
            exit_code: Some(process_exit.exit_code),
            stdout: process_exit.stdout,
            stderr: String::new(),
            command_id: Some(run.process().id().to_owned()),
        };
        let cancelled = process_exit.kill.is_some();
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
        run.process().persist_final(&response.to_wire_value());
        let command_id = run.process().id().to_owned();
        let caller_id = run.process().caller_id().to_owned();
        let command = run.process().command().to_owned();
        self.registry.remove(&command_id);
        if publish_completion {
            self.registry.push_completed(CommandCompletion {
                command_id,
                caller_id,
                command,
                result: response.clone(),
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
