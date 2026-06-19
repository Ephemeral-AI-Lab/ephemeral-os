use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

use command::process::{CommandProcess, CommandProcessSpawn, CommandProcessSpec};
use command::yield_wait_loop::{wait_for_yield_with_timing, WaitOutcome};
use command::{CommandError, StartCommand};
use serde_json::json;
use workspace::profile::WorkspaceModeContext;

use crate::command::command_workspace::OneShotCommandWorkspace;
use crate::command::contract::{CommandResponse, CommandStatus};
use crate::command::finalize::insert_cgroup_process_resource_timings;
use crate::command::outcome::WorkspaceTimings;
use crate::command::prepare::{
    prepare_one_shot, prepare_workspace, PrepareInputs, PreparedCommand,
};
use crate::command::registry::{
    ActiveCommand, CommandReservation, CommandTraceOrigin, OneShotRun, WorkspaceRun,
};
use crate::command::trace::{
    command_process_wait_host_resource_stats_event, command_process_wait_resource_stats_event,
    command_process_wait_tree_resource_stats_events, command_response_trace_events,
    CommandTraceEvent,
};

use super::{
    command_prepare_error, elapsed_ms, CommandExecError, CommandExecOutcome, CommandOps, ExecTarget,
};

impl CommandOps {
    pub fn exec_command_with_trace(
        &self,
        request: StartCommand,
        target: ExecTarget,
    ) -> Result<CommandExecOutcome, CommandExecError> {
        if request.cmd.trim().is_empty() {
            return Err(CommandError::InvalidRequest("cmd must be non-empty".to_owned()).into());
        }
        let reservation = self
            .registry
            .try_reserve()
            .map_err(|error| CommandError::InvalidRequest(error.to_string()))?;
        let id = self.registry.next_id();
        let yield_time_ms = request.yield_time_ms;
        let spec = CommandProcessSpec {
            id: id.clone(),
            caller_id: request.caller_id.clone(),
            command: request.cmd.clone(),
            timeout_seconds: request.timeout_seconds,
        };
        match target {
            ExecTarget::OneShot {
                workspace,
                scratch_root,
            } => self.start_one_shot(
                reservation,
                spec,
                &request,
                &id,
                workspace,
                scratch_root,
                yield_time_ms,
            ),
            ExecTarget::Workspace { context } => {
                self.start_workspace(reservation, spec, &request, &id, context, yield_time_ms)
            }
        }
    }

    #[expect(
        clippy::too_many_arguments,
        reason = "start inputs are one-shot plumbing from the typed target"
    )]
    fn start_one_shot(
        &self,
        reservation: CommandReservation,
        spec: CommandProcessSpec,
        request: &StartCommand,
        command_id: &str,
        workspace: Box<OneShotCommandWorkspace>,
        scratch_root: PathBuf,
        yield_time_ms: u64,
    ) -> Result<CommandExecOutcome, CommandExecError> {
        let result = {
            let prepared = prepare_one_shot(
                PrepareInputs {
                    caller_id: &request.caller_id,
                    command_id,
                    invocation_id: &request.invocation_id,
                    cmd: &request.cmd,
                    cwd: request.cwd.as_deref(),
                    remountable: false,
                    timeout_seconds: request.timeout_seconds,
                    command_dir: scratch_root.join(command_id),
                    workspace_label: "one_shot",
                },
                workspace.workspace_root(),
                &workspace.snapshot().layer_paths,
                workspace.dirs(),
                &workspace.dirs().run_dir,
            )
            .map_err(command_prepare_error)?;
            let mut trace_events = prepared.trace_events.clone();
            let normalization = workspace.normalization();
            trace_events.push(CommandTraceEvent::new(
                "command_snapshot_normalized",
                json!({
                    "triggered": normalization.triggered,
                    "max_depth": self.commit_options.auto_squash_max_depth,
                    "active_depth_before": normalization.active_depth_before,
                    "active_depth_after": normalization.active_depth_after,
                    "checkpoint_count": normalization.checkpoint_count,
                    "removed_layer_count": normalization.removed_layer_count,
                    "bytes_added": normalization.bytes_added,
                    "protected_layer_count": normalization.protected_layer_count,
                    "protected_pinned_bytes": normalization.protected_pinned_bytes,
                    "lease_layer_count": workspace.snapshot().layer_paths.len(),
                }),
            ));
            let process = self.spawn_process(spec, prepared, &mut trace_events)?;
            Ok((process, trace_events))
        };
        let (process, trace_events) = match result {
            Ok(parts) => parts,
            Err(error) => {
                let _ = workspace.release_lease();
                return Err(error);
            }
        };
        let workspace = *workspace;
        let trace_origin = CommandTraceOrigin::from_start(request);
        Ok(self.register_and_wait(
            reservation,
            process,
            yield_time_ms,
            move |process| {
                ActiveCommand::OneShot(OneShotRun {
                    process,
                    trace_origin,
                    workspace,
                })
            },
            trace_events,
        ))
    }

    fn start_workspace(
        &self,
        reservation: CommandReservation,
        spec: CommandProcessSpec,
        request: &StartCommand,
        command_id: &str,
        mode_context: Box<WorkspaceModeContext>,
        yield_time_ms: u64,
    ) -> Result<CommandExecOutcome, CommandExecError> {
        let prepared = prepare_workspace(
            PrepareInputs {
                caller_id: &request.caller_id,
                command_id,
                invocation_id: &request.invocation_id,
                cmd: &request.cmd,
                cwd: request.cwd.as_deref(),
                remountable: request.remountable,
                timeout_seconds: request.timeout_seconds,
                command_dir: mode_context.scratch_dir.join("commands").join(command_id),
                workspace_label: "workspace",
            },
            &mode_context,
        )
        .map_err(command_prepare_error)?;
        let mut trace_events = prepared.trace_events.clone();
        let process = self.spawn_process(spec, prepared, &mut trace_events)?;
        let mode_context = *mode_context;
        let trace_origin = CommandTraceOrigin::from_start(request);
        Ok(self.register_and_wait(
            reservation,
            process,
            yield_time_ms,
            move |process| {
                ActiveCommand::Workspace(WorkspaceRun {
                    process,
                    trace_origin,
                    context: mode_context,
                    remountable: request.remountable,
                })
            },
            trace_events,
        ))
    }

    pub(super) fn spawn_process(
        &self,
        spec: CommandProcessSpec,
        prepared: PreparedCommand,
        trace_events: &mut Vec<CommandTraceEvent>,
    ) -> Result<CommandProcess, CommandExecError> {
        let command_id = spec.id.clone();
        let request_path = prepared.request_path.clone();
        let spawn_started = Instant::now();
        let process = match CommandProcess::spawn(
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
        ) {
            Ok(process) => process,
            Err(error) => {
                trace_events.push(CommandTraceEvent::new(
                    "spawned",
                    json!({
                        "command_id": command_id,
                        "success": false,
                        "duration_ms": elapsed_ms(spawn_started),
                        "error": error.to_string(),
                    }),
                ));
                if let CommandError::ArtifactWrite {
                    artifact,
                    path,
                    error,
                } = &error
                {
                    trace_events.push(CommandTraceEvent::artifact_failed(artifact, path, error));
                }
                return Err(CommandExecError::with_trace_events(
                    error,
                    trace_events.clone(),
                ));
            }
        };
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
                "success": true,
                "duration_ms": elapsed_ms(spawn_started),
            }),
        ));
        Ok(process)
    }

    fn register_and_wait(
        &self,
        reservation: CommandReservation,
        process: CommandProcess,
        yield_time_ms: u64,
        make_run: impl FnOnce(CommandProcess) -> ActiveCommand,
        mut trace_events: Vec<CommandTraceEvent>,
    ) -> CommandExecOutcome {
        let id = process.id().to_owned();
        let run = Arc::new(make_run(process));
        reservation.activate(Arc::clone(&run));
        let mut before_resource_timings = WorkspaceTimings::new();
        insert_cgroup_process_resource_timings(&mut before_resource_timings);
        self.store_before_resource_sample(&id, before_resource_timings);
        let wait_started = Instant::now();
        let wait_report = self.wait_on_run_with_timing(run, yield_time_ms, 0, false, |stdout| {
            CommandResponse::running(id.clone(), stdout)
        });
        let response = wait_report.response;
        let timing = wait_report.timing;
        trace_events.push(CommandTraceEvent::new(
            "wait_finished",
            json!({
                "command_id": response.command_id.as_ref().map(ToString::to_string),
                "status": response.status.as_str(),
                "completed": response.status != CommandStatus::Running,
                "yield_time_ms": yield_time_ms,
                "duration_ms": elapsed_ms(wait_started),
                "wait_loop_duration_ms": timing.elapsed_ms,
                "wait_yield_reason": timing.reason.as_str(),
                "configured_quiet_ms": self.config.quiet_ms,
                "first_output_ms": timing.first_output_ms,
                "last_output_ms": timing.last_output_ms,
                "quiet_ms": timing.quiet_ms,
            }),
        ));
        if let Some(finalized) = &response.finalized {
            if let Some(before_resource_timings) = self.take_before_resource_sample(&id) {
                trace_events.push(command_process_wait_resource_stats_event(
                    "before",
                    &before_resource_timings,
                ));
                trace_events.push(command_process_wait_host_resource_stats_event(
                    "before",
                    &before_resource_timings,
                ));
                trace_events.push(command_process_wait_resource_stats_event(
                    "after",
                    &finalized.core.timings,
                ));
                trace_events.push(command_process_wait_host_resource_stats_event(
                    "after",
                    &finalized.core.timings,
                ));
            }
            trace_events.extend(command_process_wait_tree_resource_stats_events(
                &finalized.core.timings,
            ));
            trace_events.extend(command_response_trace_events(&response));
        } else if response.status != CommandStatus::Running {
            // Finalized without facts (discarded work): the sample has no
            // "after" counterpart on this surface; drop it so it cannot leak.
            let _ = self.take_before_resource_sample(&id);
        }
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

    pub(super) fn wait_on_run(
        &self,
        run: Arc<ActiveCommand>,
        wait_ms: u64,
        start_offset: u64,
        consume_resource_pair: bool,
        on_running: impl FnOnce(String) -> CommandResponse,
    ) -> CommandResponse {
        self.wait_on_run_with_timing(
            run,
            wait_ms,
            start_offset,
            consume_resource_pair,
            on_running,
        )
        .response
    }

    fn wait_on_run_with_timing(
        &self,
        run: Arc<ActiveCommand>,
        wait_ms: u64,
        start_offset: u64,
        consume_resource_pair: bool,
        on_running: impl FnOnce(String) -> CommandResponse,
    ) -> WaitOnRunReport {
        let report = wait_for_yield_with_timing(run.process(), &self.config, wait_ms, start_offset);
        let response = match report.outcome {
            WaitOutcome::Completed(process_exit) => {
                self.finalize_command(run, process_exit, false, consume_resource_pair)
            }
            WaitOutcome::Running(stdout) => on_running(stdout),
        };
        WaitOnRunReport {
            response,
            timing: report.timing,
        }
    }
}

struct WaitOnRunReport {
    response: CommandResponse,
    timing: command::yield_wait_loop::WaitTiming,
}
