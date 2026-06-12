//! One command process: the child process, PTY transcript, kill/reap state,
//! and final-response persistence.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use serde_json::Value;

pub use crate::pty::KillReason;

use crate::pty::{
    spawn_current_exe_ns_runner, CommandCompletionStatus, CommandRunnerResult, ProcessReap,
    PtyProcess,
};
use crate::transcript::{read_transcript_since, read_transcript_stdout, read_transcript_tail};
use crate::yield_wait_loop::CommandWaitTarget;
use crate::CommandError;

#[cfg(test)]
#[path = "../tests/unit/process.rs"]
mod tests;

/// PTY/process substrate for one command. It owns the child process, transcript,
/// and cancel flag, but no workspace policy: the run that owns this process
/// decides publish-vs-discard.
pub struct CommandProcess {
    id: String,
    caller_id: String,
    command: String,
    started_at: Instant,
    timeout: Option<Duration>,
    runtime: CommandProcessRuntime,
}

pub struct CommandProcessSpec {
    pub id: String,
    pub caller_id: String,
    pub command: String,
    pub timeout_seconds: Option<f64>,
}

pub struct CommandProcessSpawn<'a> {
    pub run_request: Value,
    pub request_path: PathBuf,
    pub output_path: PathBuf,
    pub final_path: PathBuf,
    pub transcript_path: PathBuf,
    pub transcript_timestamp_timezone: &'a str,
    pub output_drain_grace_ms: u64,
}

/// The raw, policy-free result of reaping a finished command process. The
/// substrate produces this; the owning workspace run turns it into a
/// rendered operation response by publishing (complete) or discarding (cancel).
/// Keeping the publish/discard decision out of the process is the structural
/// guarantee that a cancelled command never reaches the OCC merge.
#[derive(Debug, Clone)]
pub struct ReapedCommand {
    pub status: String,
    pub exit_code: i64,
    pub runner_result: Option<Value>,
    pub stdout: String,
    pub elapsed_s: f64,
    /// Why the substrate killed this process, if it did. `None` is a natural
    /// exit; `Some(_)` means a kill (cancel or timeout) and the owning run
    /// DISCARDS rather than publishes.
    pub kill: Option<KillReason>,
}

struct RunningCommandProcessParts {
    process: PtyProcess,
    output_path: PathBuf,
    final_path: PathBuf,
    transcript_path: PathBuf,
    output_drain_grace_ms: u64,
}

/// Per-command process state: the child, its paths, and the kill/reap flags.
struct CommandProcessRuntime {
    process: PtyProcess,
    output_path: PathBuf,
    final_path: PathBuf,
    transcript_path: PathBuf,
    /// Why this process was killed, if it has been. Set once by `cancel_process`
    /// (user cancel) or `time_out_process` (deadline backstop); a user cancel
    /// wins, so a cancelled command is never relabeled as timed-out.
    kill: Mutex<Option<KillReason>>,
    output_drain_grace_ms: u64,
    /// Reaped-once guard so two pollers can't both finalize the same child.
    reaped: Mutex<bool>,
}

impl CommandProcessRuntime {
    fn new(parts: RunningCommandProcessParts) -> Self {
        Self {
            process: parts.process,
            output_path: parts.output_path,
            final_path: parts.final_path,
            transcript_path: parts.transcript_path,
            kill: Mutex::new(None),
            output_drain_grace_ms: parts.output_drain_grace_ms,
            reaped: Mutex::new(false),
        }
    }

    /// `/dev/null`-backed runtime so scaffold processes can exist in tests
    /// without a live child.
    fn inactive() -> Self {
        let writer = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open("/dev/null")
            .expect("open /dev/null for inactive command process");
        Self::new(RunningCommandProcessParts {
            process: PtyProcess::inactive(writer),
            output_path: PathBuf::new(),
            final_path: PathBuf::new(),
            transcript_path: PathBuf::new(),
            output_drain_grace_ms: 0,
        })
    }
}

impl CommandProcess {
    /// Process-free scaffold for registry and identity tests.
    #[must_use]
    pub fn new(spec: CommandProcessSpec) -> Self {
        Self::with_runtime(spec, CommandProcessRuntime::inactive())
    }

    #[must_use]
    fn new_running(spec: CommandProcessSpec, parts: RunningCommandProcessParts) -> Self {
        Self::with_runtime(spec, CommandProcessRuntime::new(parts))
    }

    pub fn spawn(
        spec: CommandProcessSpec,
        parts: CommandProcessSpawn<'_>,
    ) -> Result<Self, CommandError> {
        let process = spawn_current_exe_ns_runner(
            &parts.request_path,
            &parts.run_request,
            &parts.output_path,
            parts.transcript_path.clone(),
            parts.transcript_timestamp_timezone,
        )?;
        Ok(Self::new_running(
            spec,
            RunningCommandProcessParts {
                process,
                output_path: parts.output_path,
                final_path: parts.final_path,
                transcript_path: parts.transcript_path,
                output_drain_grace_ms: parts.output_drain_grace_ms,
            },
        ))
    }

    fn with_runtime(spec: CommandProcessSpec, runtime: CommandProcessRuntime) -> Self {
        Self {
            id: spec.id,
            caller_id: spec.caller_id,
            command: spec.command,
            started_at: Instant::now(),
            timeout: spec.timeout_seconds.and_then(duration_from_secs_f64),
            runtime,
        }
    }

    #[must_use]
    pub fn id(&self) -> &str {
        &self.id
    }

    #[must_use]
    pub fn caller_id(&self) -> &str {
        &self.caller_id
    }

    #[must_use]
    pub fn command(&self) -> &str {
        &self.command
    }

    #[cfg(test)]
    #[must_use]
    pub const fn started_at(&self) -> Instant {
        self.started_at
    }

    pub fn write_process_stdin(&self, chars: &str) -> Result<(), CommandError> {
        self.runtime.process.write_stdin(chars.as_bytes())?;
        Ok(())
    }

    /// Cancel at a caller's request (Ctrl-C/Ctrl-D, the cancel op, or run
    /// teardown): record the reason and kill the process group. A cancel always
    /// wins over a later timeout mark.
    pub fn cancel_process(&self) {
        *lock(&self.runtime.kill) = Some(KillReason::Cancelled);
        self.runtime.process.terminate();
    }

    /// Kill a command that exceeded its deadline (the reaper backstop). Records
    /// `TimedOut` only if no kill reason is set yet, so a prior user cancel keeps
    /// its `Cancelled` label; either way the process group is killed.
    pub fn time_out_process(&self) {
        {
            let mut kill = lock(&self.runtime.kill);
            if kill.is_none() {
                *kill = Some(KillReason::TimedOut);
            }
        }
        self.runtime.process.terminate();
    }

    #[must_use]
    pub fn read_recent_output(&self, last_n_lines: usize) -> String {
        read_transcript_tail(&self.runtime.transcript_path, last_n_lines)
    }

    #[must_use]
    pub fn read_output_since(&self, start_offset: u64) -> String {
        read_transcript_since(&self.runtime.transcript_path, start_offset)
    }

    #[must_use]
    pub fn transcript_len(&self) -> u64 {
        transcript_len(&self.runtime.transcript_path)
    }

    #[must_use]
    pub fn is_past_deadline(&self, now: Instant, max_command_s: u64) -> bool {
        let timeout = self
            .timeout
            .unwrap_or_else(|| Duration::from_secs(max_command_s));
        now.duration_since(self.started_at) >= timeout
    }

    /// Reap the child if it has exited, returning the raw command result. Returns
    /// `None` while the process is still running or has already been reaped. This
    /// only reaps the substrate — it does not publish or discard; the owning run
    /// decides that from `ReapedCommand::kill`.
    pub fn reap(&self) -> Option<ReapedCommand> {
        let mut reaped = lock(&self.runtime.reaped);
        if *reaped {
            return None;
        }
        let process_exit = match self.runtime.process.try_reap() {
            ProcessReap::Running => return None,
            ProcessReap::Exited(exit) => exit,
        };
        *reaped = true;
        drop(reaped);
        self.runtime.process.terminate();
        self.runtime
            .process
            .wait_for_reader_done(Duration::from_millis(self.runtime.output_drain_grace_ms));
        let runner = CommandRunnerResult::read_from_path(&self.runtime.output_path);
        let kill = *lock(&self.runtime.kill);
        let completion =
            CommandCompletionStatus::from_process_and_runner(process_exit, runner.as_ref(), kill);
        Some(ReapedCommand {
            status: completion.status().to_owned(),
            exit_code: completion.exit_code(),
            runner_result: runner.map(|runner| runner.value().clone()),
            stdout: self.final_stdout(),
            elapsed_s: self.started_at.elapsed().as_secs_f64(),
            kill,
        })
    }

    /// Persist the run's final response to `final_path` for crash recovery and
    /// remove the transcript. Best-effort: `final_path` is only a crash-recovery
    /// convenience, so a write failure does not undo the already-decided
    /// publish/discard or fail the operation.
    pub fn persist_final(&self, response: &serde_json::Value) {
        let _ = write_final_response(&self.runtime.final_path, response);
        self.remove_transcript_file();
    }

    fn remove_transcript_file(&self) {
        if self.runtime.transcript_path.as_os_str().is_empty() {
            return;
        }
        let _ = std::fs::remove_file(&self.runtime.transcript_path);
    }

    fn final_stdout(&self) -> String {
        read_transcript_stdout(&self.runtime.transcript_path)
    }
}

impl CommandWaitTarget<ReapedCommand> for CommandProcess {
    fn try_finalize(&self) -> Option<ReapedCommand> {
        self.reap()
    }

    fn transcript_len(&self) -> u64 {
        Self::transcript_len(self)
    }

    fn read_output_since(&self, start_offset: u64) -> String {
        Self::read_output_since(self, start_offset)
    }
}

fn duration_from_secs_f64(seconds: f64) -> Option<Duration> {
    if seconds.is_finite() && seconds > 0.0 {
        Some(Duration::from_secs_f64(seconds))
    } else {
        None
    }
}

fn transcript_len(path: &Path) -> u64 {
    if path.as_os_str().is_empty() {
        return 0;
    }
    std::fs::metadata(path).map_or(0, |metadata| metadata.len())
}

fn write_final_response(path: &Path, response: &serde_json::Value) -> Result<(), CommandError> {
    if path.as_os_str().is_empty() {
        return Ok(());
    }
    let bytes = serde_json::to_vec_pretty(response).map_err(|error| {
        CommandError::InvalidRequest(format!("serialize final command response: {error}"))
    })?;
    std::fs::write(path, bytes)?;
    Ok(())
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}
