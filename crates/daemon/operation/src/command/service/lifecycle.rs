use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use command::process::{
    CommandProcessExit, CommandProcessMetadata, KillReason, PROCESS_METADATA_FILE,
};
use command::CollectCompleted;
use layerstack::service;
use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use serde_json::json;
use serde_json::Value;
use trace::{
    EventRecord, SpanKind, SpanRecord, SpanUid, TraceId, TraceKind, TraceLink, TraceLinkKind,
    TraceRecord,
};

use crate::command::contract::{
    CollectCompletedOutput, CommandCompletion, CommandResponse, CommandStatus,
    PUBLISH_LANES_METADATA_KEY,
};
use crate::command::finalize::{
    discarded_response, finalization_error_response,
    finalize_ephemeral_command_with_capture_options, finalize_isolated_command,
};
use crate::command::outcome::FinalizeCommandRequest;
use crate::command::registry::ActiveCommand;
use crate::command::trace::{
    active_command_advance_trace_record, append_resource_pair_to_record,
    command_finalize_trace_record, unix_now_ms, CommandFinalizeTraceFacts, FinalizedCommand,
};
use crate::WorkspaceKind;

use super::CommandOps;

impl CommandOps {
    #[must_use]
    pub fn count_by_caller(&self, caller_id: Option<&str>) -> usize {
        self.registry.count_by_caller(caller_id)
    }

    #[must_use]
    pub fn collect_completed(&self, request: &CollectCompleted) -> CollectCompletedOutput {
        self.registry.collect_completed(request)
    }

    pub fn push_completed(&self, completion: CommandCompletion) {
        let evictions = self.registry.push_completed(completion);
        if evictions.is_empty() {
            return;
        }
        // Pushes outside a finalization (orphan recovery) still record their
        // eviction loss markers as a standalone background root.
        let now = unix_now_ms();
        let mut record = TraceRecord::new(TraceId::new(), SpanUid::ROOT);
        record.kind = TraceKind::CommandFinalize;
        record.started_at_unix_ms = now;
        record.finished_at_unix_ms = now;
        let mut span = SpanRecord::new(
            SpanUid::ROOT,
            None,
            "command.finalize",
            SpanKind::CommandFinalize,
            json!({"source": "completion_buffer_eviction"}),
        );
        span.started_at_unix_ms = now;
        span.finished_at_unix_ms = now;
        record.spans.push(span);
        for eviction in evictions {
            let mut event = EventRecord::new(
                SpanUid::ROOT,
                "completion_buffer_evicted",
                "command",
                json!({
                    "command_id": eviction.command_id.clone(),
                    "seq": eviction.seq,
                    "max_entries": eviction.max_entries,
                }),
            );
            event.at_unix_ms = now;
            record.links.push(TraceLink {
                kind: TraceLinkKind::Command,
                value: eviction.command_id,
            });
            record.events.push(event);
        }
        self.push_pending_finalize_record(record);
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
                    let _ = self.finalize_command(Arc::clone(run), process_exit, false, true);
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
                let _ = self.finalize_command(run, process_exit, false, true);
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
        let _ = self.take_before_resource_sample(run.process().id());
        if let ActiveCommand::Ephemeral(ephemeral) = &**run {
            let _ = service::release_lease(&ephemeral.root, &ephemeral.snapshot.lease_id);
        }
    }

    pub fn advance_active_commands_once(&self, now: Instant) -> Vec<TraceRecord> {
        let mut records = self.take_pending_finalize_records();
        let mut live_count = 0usize;
        let mut timed_out_commands = Vec::new();
        let mut finalized_commands = Vec::new();
        for run in self.registry.live() {
            live_count += 1;
            let command_id = run.process().id().to_owned();
            if run
                .process()
                .is_past_deadline(now, self.config.max_command_s)
            {
                run.process().time_out_process();
                timed_out_commands.push(command_id.clone());
            }
            if let Some(process_exit) = run.process().take_exit() {
                let publish_completion = process_exit.kill != Some(KillReason::Cancelled);
                finalized_commands.push(command_id);
                records.push(self.finalize_command_record(run, process_exit, publish_completion));
            }
        }
        if !timed_out_commands.is_empty() || !finalized_commands.is_empty() {
            records.insert(
                0,
                active_command_advance_trace_record(
                    live_count,
                    timed_out_commands,
                    finalized_commands,
                ),
            );
        }
        records
    }

    pub fn recover_orphaned_commands(&self) {
        let dir = self.scratch_root();
        let Ok(entries) = std::fs::read_dir(&dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let Some((id, meta)) = read_recoverable_command_metadata(&path) else {
                continue;
            };
            let recovered_process = classify_recovered_command(&path);
            terminate_recovered_process_group(recovered_process.process_group_id());
            let caller_id = meta
                .get("caller_id")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let command = meta
                .get("command")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let result = CommandResponse {
                status: CommandStatus::Error,
                exit_code: Some(1),
                stdout: String::new(),
                stderr: recovered_process.stderr().to_owned(),
                command_id: Some(crate::CommandId::new(id.clone())),
                finalized: None,
            };
            self.push_completed(CommandCompletion {
                command_id: id,
                caller_id: caller_id.to_owned(),
                command: command.to_owned(),
                result,
            });
            let _ = std::fs::remove_dir_all(&path);
        }
    }

    /// Every foreground finalization also produces a `CommandFinalize` record
    /// for the background spool: the request sidecar carries the facts the
    /// caller paid for, while exit/persistence/eviction facts stay durable
    /// even when no later request observes the command.
    pub(super) fn finalize_command(
        &self,
        run: Arc<ActiveCommand>,
        process_exit: CommandProcessExit,
        publish_completion: bool,
        consume_resource_pair: bool,
    ) -> CommandResponse {
        let command_id = run.process().id().to_owned();
        let finalized = self.finalize_command_inner(run, process_exit, publish_completion);
        let mut record = command_finalize_trace_record(&finalized.trace);
        if consume_resource_pair {
            if let Some(before) = self.take_before_resource_sample(&command_id) {
                append_resource_pair_to_record(&mut record, &before, &finalized.response);
            }
        }
        self.push_pending_finalize_record(record);
        finalized.response
    }

    pub(super) fn finalize_command_record(
        &self,
        run: Arc<ActiveCommand>,
        process_exit: CommandProcessExit,
        publish_completion: bool,
    ) -> TraceRecord {
        let command_id = run.process().id().to_owned();
        let finalized = self.finalize_command_inner(run, process_exit, publish_completion);
        let mut record = command_finalize_trace_record(&finalized.trace);
        if let Some(before) = self.take_before_resource_sample(&command_id) {
            append_resource_pair_to_record(&mut record, &before, &finalized.response);
        }
        record
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
        let request_for_error = request.clone();
        let (workspace_kind, route_manifest_version, outcome) = match &*run {
            ActiveCommand::Ephemeral(ephemeral) => {
                let outcome = finalize_ephemeral_command_with_capture_options(
                    &ephemeral.root,
                    &ephemeral.snapshot,
                    &ephemeral.workspace,
                    self.commit_options(),
                    self.capture_options(),
                    request,
                );
                let _ = service::release_lease(&ephemeral.root, &ephemeral.snapshot.lease_id);
                (
                    WorkspaceKind::Ephemeral,
                    Some(ephemeral.snapshot.manifest_version),
                    outcome,
                )
            }
            ActiveCommand::Isolated(isolated) => {
                if kill.is_some() {
                    (
                        WorkspaceKind::Isolated,
                        Some(isolated.binding.manifest_version),
                        Ok(discarded_response(
                            WorkspaceKind::Isolated,
                            request,
                            Some(isolated.binding.manifest_version),
                        )),
                    )
                } else {
                    (
                        WorkspaceKind::Isolated,
                        Some(isolated.binding.manifest_version),
                        finalize_isolated_command(&isolated.binding, request),
                    )
                }
            }
        };
        let response = match outcome {
            Ok(response) => response,
            Err(error) => finalization_error_response(
                workspace_kind,
                request_for_error,
                route_manifest_version,
                error,
            ),
        };
        let publish_lanes = response
            .finalized
            .as_ref()
            .and_then(|finalized| finalized.extras.get(PUBLISH_LANES_METADATA_KEY).cloned());
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
                publish_lanes,
            },
            response,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum RecoveredCommandProcess {
    PreparedNeverStarted,
    SpawnedTracked { process_group_id: Option<i32> },
    MalformedProcessMetadata { error: String },
}

impl RecoveredCommandProcess {
    const fn process_group_id(&self) -> Option<i32> {
        match self {
            Self::SpawnedTracked { process_group_id } => *process_group_id,
            Self::PreparedNeverStarted | Self::MalformedProcessMetadata { .. } => None,
        }
    }

    fn stderr(&self) -> &str {
        match self {
            Self::PreparedNeverStarted => {
                "orphan_recovered: prepared command never started before daemon restart"
            }
            Self::SpawnedTracked { .. } => "orphan_recovered: daemon restarted",
            Self::MalformedProcessMetadata { .. } => {
                "orphan_recovered: malformed process metadata after daemon restart"
            }
        }
    }
}

fn classify_recovered_command(command_dir: &std::path::Path) -> RecoveredCommandProcess {
    let path = command_dir.join(PROCESS_METADATA_FILE);
    let bytes = match std::fs::read(&path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return RecoveredCommandProcess::PreparedNeverStarted;
        }
        Err(error) => {
            return RecoveredCommandProcess::MalformedProcessMetadata {
                error: error.to_string(),
            };
        }
    };
    match CommandProcessMetadata::from_slice(&bytes) {
        Ok(metadata) => RecoveredCommandProcess::SpawnedTracked {
            process_group_id: metadata.process_group_id.filter(|pgid| *pgid > 0),
        },
        Err(error) => RecoveredCommandProcess::MalformedProcessMetadata {
            error: error.to_string(),
        },
    }
}

fn read_recoverable_command_metadata(command_dir: &std::path::Path) -> Option<(String, Value)> {
    let bytes = std::fs::read(command_dir.join("metadata.json")).ok()?;
    let meta = serde_json::from_slice::<Value>(&bytes).ok()?;
    let id = meta.get("command_id")?.as_str()?.trim().to_owned();
    (!id.is_empty()).then_some((id, meta))
}

fn terminate_recovered_process_group(process_group_id: Option<i32>) {
    let Some(pgid) = process_group_id else {
        return;
    };
    let pid = Pid::from_raw(pgid);
    if killpg(pid, Signal::SIGTERM).is_ok() {
        thread::sleep(Duration::from_millis(50));
    }
    let _ = killpg(pid, Signal::SIGKILL);
}

#[cfg(test)]
mod recovery_tests {
    use super::*;

    #[test]
    fn recovered_process_group_id_reads_positive_i32() -> Result<(), Box<dyn std::error::Error>> {
        let root = std::env::temp_dir().join(format!(
            "operation-command-recovery-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_nanos()
        ));
        std::fs::create_dir_all(&root)?;
        std::fs::write(
            root.join(PROCESS_METADATA_FILE),
            serde_json::to_vec(&CommandProcessMetadata::new(Some(12345)))?,
        )?;

        assert_eq!(
            classify_recovered_command(&root).process_group_id(),
            Some(12345)
        );

        std::fs::write(
            root.join(PROCESS_METADATA_FILE),
            serde_json::to_vec(&CommandProcessMetadata::new(Some(0)))?,
        )?;
        assert_eq!(classify_recovered_command(&root).process_group_id(), None);
        std::fs::write(
            root.join(PROCESS_METADATA_FILE),
            br#"{"process_group_id":2147483648}"#,
        )?;
        assert_eq!(classify_recovered_command(&root).process_group_id(), None);

        let _ = std::fs::remove_dir_all(root);
        Ok(())
    }

    #[test]
    fn classify_recovered_command_distinguishes_missing_and_malformed_process_metadata(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let root = std::env::temp_dir().join(format!(
            "operation-command-recovery-classify-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_nanos()
        ));
        std::fs::create_dir_all(&root)?;

        assert_eq!(
            classify_recovered_command(&root),
            RecoveredCommandProcess::PreparedNeverStarted
        );

        std::fs::write(root.join(PROCESS_METADATA_FILE), b"{not-json")?;
        assert!(matches!(
            classify_recovered_command(&root),
            RecoveredCommandProcess::MalformedProcessMetadata { .. }
        ));

        let _ = std::fs::remove_dir_all(root);
        Ok(())
    }
}
