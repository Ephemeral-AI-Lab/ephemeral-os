//! One command process: the child process, PTY transcript, kill/exit state,
//! and final-response persistence.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub use crate::pty::KillReason;

use crate::pty::{
    spawn_current_exe_ns_runner, CommandCompletionStatus, CommandRunnerResult, PtyProcess,
    PtyProcessExit,
};
use crate::transcript::{read_full_transcript_stdout, read_transcript_since, read_transcript_tail};
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

pub const PROCESS_METADATA_FILE: &str = "process.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct CommandProcessMetadata {
    pub process_group_id: Option<i32>,
}

impl CommandProcessMetadata {
    #[must_use]
    pub const fn new(process_group_id: Option<i32>) -> Self {
        Self { process_group_id }
    }

    /// Parse durable process metadata written next to command artifacts.
    ///
    /// # Errors
    /// Returns an error when the bytes are not valid process metadata JSON.
    pub fn from_slice(bytes: &[u8]) -> serde_json::Result<Self> {
        serde_json::from_slice(bytes)
    }

    fn to_pretty_vec(self) -> Result<Vec<u8>, CommandError> {
        serde_json::to_vec_pretty(&self).map_err(|error| {
            CommandError::InvalidRequest(format!("serialize process metadata: {error}"))
        })
    }
}

/// The raw, policy-free result of a finished command process. The substrate
/// produces this; the owning workspace run turns it into a
/// rendered operation response by publishing (complete) or discarding (cancel).
/// Keeping the publish/discard decision out of the process is the structural
/// guarantee that a cancelled command never reaches the OCC merge.
#[derive(Debug, Clone)]
pub struct CommandProcessExit {
    pub status: String,
    pub exit_code: i64,
    pub signal: Option<i32>,
    pub runner_result: Option<Value>,
    pub stdout: String,
    pub elapsed_s: f64,
    /// Why the substrate killed this process, if it did. `None` is a natural
    /// exit; `Some(_)` means a kill (cancel or timeout) and the owning run
    /// DISCARDS rather than publishes.
    pub kill: Option<KillReason>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandPersistenceOutcome {
    pub final_response: Option<CommandFinalResponsePersistence>,
    pub transcript_error: Option<CommandTranscriptPersistenceError>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommandFinalResponsePersistence {
    Persisted { path: PathBuf, bytes: usize },
    Failed { path: PathBuf, error: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandTranscriptPersistenceError {
    pub path: PathBuf,
    pub error: String,
}

/// Per-command process state: the child, its paths, and the kill/exit flags.
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
    /// Exit-taken guard so two pollers can't both finalize the same child.
    exit_taken: Mutex<bool>,
}

impl CommandProcessRuntime {
    fn new(
        process: PtyProcess,
        output_path: PathBuf,
        final_path: PathBuf,
        transcript_path: PathBuf,
        output_drain_grace_ms: u64,
    ) -> Self {
        Self {
            process,
            output_path,
            final_path,
            transcript_path,
            kill: Mutex::new(None),
            output_drain_grace_ms,
            exit_taken: Mutex::new(false),
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
        Self::new(
            PtyProcess::inactive(writer),
            PathBuf::new(),
            PathBuf::new(),
            PathBuf::new(),
            0,
        )
    }
}

impl CommandProcess {
    #[doc(hidden)]
    #[must_use]
    pub fn inactive_for_test(spec: CommandProcessSpec) -> Self {
        Self::with_runtime(spec, CommandProcessRuntime::inactive())
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
        let process_path = process_metadata_path(&parts.final_path)?;
        if let Err(error) = write_process_metadata(&process_path, process.process_group_id()) {
            process.terminate();
            return Err(error);
        }
        let process = process.allow_start().map_err(|error| {
            CommandError::artifact_write("process_start_ack", &process_path, error)
        })?;
        Ok(Self::with_runtime(
            spec,
            CommandProcessRuntime::new(
                process,
                parts.output_path,
                parts.final_path,
                parts.transcript_path,
                parts.output_drain_grace_ms,
            ),
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

    #[must_use]
    pub fn process_group_id(&self) -> Option<i32> {
        self.runtime.process.process_group_id()
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

    /// Kill a command that exceeded its deadline (the deadline backstop). Records
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

    /// Take the child exit if it has completed, returning the raw command
    /// result. Returns `None` while the process is still running or the exit has
    /// already been taken. This only takes the process exit; it does not publish
    /// or discard. The owning run decides that from `CommandProcessExit::kill`.
    pub fn take_exit(&self) -> Option<CommandProcessExit> {
        let mut exit_taken = lock(&self.runtime.exit_taken);
        if *exit_taken {
            return None;
        }
        let process_exit = match self.runtime.process.take_exit() {
            PtyProcessExit::Running => return None,
            PtyProcessExit::Exited(exit) => exit,
        };
        *exit_taken = true;
        drop(exit_taken);
        self.runtime.process.terminate();
        self.runtime
            .process
            .wait_for_reader_done(Duration::from_millis(self.runtime.output_drain_grace_ms));
        let runner = CommandRunnerResult::read_from_path(&self.runtime.output_path);
        let kill = *lock(&self.runtime.kill);
        let completion =
            CommandCompletionStatus::from_process_and_runner(process_exit, runner.as_ref(), kill);
        Some(CommandProcessExit {
            status: completion.status().to_owned(),
            exit_code: completion.exit_code(),
            signal: process_exit.signal(),
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
    pub fn persist_final(&self, response: &serde_json::Value) -> CommandPersistenceOutcome {
        CommandPersistenceOutcome {
            final_response: persist_final_response(&self.runtime.final_path, response),
            transcript_error: remove_transcript_file(&self.runtime.transcript_path),
        }
    }

    fn final_stdout(&self) -> String {
        read_full_transcript_stdout(&self.runtime.transcript_path)
    }
}

impl CommandWaitTarget<CommandProcessExit> for CommandProcess {
    fn take_exit(&self) -> Option<CommandProcessExit> {
        self.take_exit()
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

fn persist_final_response(
    path: &Path,
    response: &serde_json::Value,
) -> Option<CommandFinalResponsePersistence> {
    if path.as_os_str().is_empty() {
        return None;
    }
    match write_final_response(path, response) {
        Ok(bytes) => Some(CommandFinalResponsePersistence::Persisted {
            path: path.to_path_buf(),
            bytes,
        }),
        Err(error) => Some(CommandFinalResponsePersistence::Failed {
            path: path.to_path_buf(),
            error: error.to_string(),
        }),
    }
}

fn remove_transcript_file(path: &Path) -> Option<CommandTranscriptPersistenceError> {
    if path.as_os_str().is_empty() {
        return None;
    }
    match std::fs::remove_file(path) {
        Ok(()) => None,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => Some(CommandTranscriptPersistenceError {
            path: path.to_path_buf(),
            error: error.to_string(),
        }),
    }
}

fn write_final_response(path: &Path, response: &serde_json::Value) -> Result<usize, CommandError> {
    let bytes = serde_json::to_vec_pretty(response).map_err(|error| {
        CommandError::InvalidRequest(format!("serialize final command response: {error}"))
    })?;
    let byte_len = bytes.len();
    std::fs::write(path, bytes)?;
    Ok(byte_len)
}

fn process_metadata_path(final_path: &Path) -> Result<PathBuf, CommandError> {
    final_path
        .parent()
        .map(|parent| parent.join(PROCESS_METADATA_FILE))
        .ok_or_else(|| {
            CommandError::InvalidRequest(format!(
                "final response path has no parent: {}",
                final_path.display()
            ))
        })
}

fn write_process_metadata(path: &Path, process_group_id: Option<i32>) -> Result<(), CommandError> {
    let bytes = CommandProcessMetadata::new(process_group_id).to_pretty_vec()?;
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(path)
        .map_err(|error| CommandError::artifact_write("process_metadata", path, error))?;
    file.write_all(&bytes)
        .and_then(|()| file.sync_all())
        .map_err(|error| CommandError::artifact_write("process_metadata", path, error))?;
    if let Some(parent) = path.parent() {
        sync_directory(parent)
            .map_err(|error| CommandError::artifact_write("process_metadata_dir", parent, error))?;
    }
    Ok(())
}

fn sync_directory(path: &Path) -> std::io::Result<()> {
    match std::fs::File::open(path).and_then(|file| file.sync_all()) {
        Ok(()) => Ok(()),
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::InvalidInput | std::io::ErrorKind::Unsupported
            ) =>
        {
            Ok(())
        }
        Err(error) => Err(error),
    }
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}
