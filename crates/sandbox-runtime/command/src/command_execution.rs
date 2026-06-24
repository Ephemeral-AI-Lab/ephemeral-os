//! `CommandExecution` — the single per-command handle retained in the engine
//! registry keyed by id. It serves live reads (write/yield) and terminal reads
//! (transcript window + result), distinguished by the promise.

use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use sandbox_runtime_namespace_execution::{
    CompletionWaiter, InteractiveExecution, NamespaceExecutionError, NamespaceExecutionId,
};
use sandbox_runtime_workspace::WorkspaceSessionId;

use crate::contract::CommandTerminalResult;
use crate::transcript_rows::{
    required_transcript_window, transcript_window, CommandTranscriptWindow,
};

/// The live + terminal residue of a command: the engine handle (id + promise +
/// PTY), the transcript file path, the owning workspace session, the start
/// instant, and the read cursor. The cursor is `AtomicU64` because the registry
/// hands out `&CommandExecution` under its lock and the yield path advances it
/// through a shared reference.
pub struct CommandExecution {
    exec: InteractiveExecution<CommandTerminalResult>,
    transcript_path: Option<PathBuf>,
    workspace_session_id: WorkspaceSessionId,
    workspace_root: PathBuf,
    started_at: Instant,
    next_snapshot_offset: AtomicU64,
}

impl CommandExecution {
    #[must_use]
    pub fn new(
        exec: InteractiveExecution<CommandTerminalResult>,
        transcript_path: Option<PathBuf>,
        workspace_session_id: WorkspaceSessionId,
        workspace_root: PathBuf,
        started_at: Instant,
    ) -> Self {
        Self {
            exec,
            transcript_path,
            workspace_session_id,
            workspace_root,
            started_at,
            next_snapshot_offset: AtomicU64::new(0),
        }
    }

    #[must_use]
    pub fn id(&self) -> &NamespaceExecutionId {
        self.exec.id()
    }

    #[must_use]
    pub fn is_finished(&self) -> bool {
        self.exec.is_finished()
    }

    #[must_use]
    pub fn workspace_session_id(&self) -> &WorkspaceSessionId {
        &self.workspace_session_id
    }

    #[must_use]
    pub fn workspace_root(&self) -> &Path {
        &self.workspace_root
    }

    /// The spawned process group, for remount process-group inspection/cancel.
    #[must_use]
    pub fn pgid(&self) -> Option<i32> {
        self.exec.pgid()
    }

    /// The spawned process group, under the remount coordinator naming.
    #[must_use]
    pub fn process_group_id(&self) -> Option<i32> {
        self.exec.pgid()
    }

    pub fn write_stdin(&self, bytes: &[u8]) -> io::Result<()> {
        self.exec.write_stdin(bytes)
    }

    /// Cancel the command (caller-side `killpg`); the watcher then reports it as
    /// terminal `Cancelled`/130.
    pub fn cancel(&self) {
        self.exec.cancel();
    }

    /// A cloneable cancel action, so the caller can drop the registry lock before
    /// the kill's SIGTERM grace period.
    #[must_use]
    pub fn cancel_handle(&self) -> Arc<dyn Fn() + Send + Sync> {
        self.exec.cancel_handle()
    }

    #[must_use]
    pub fn output_len(&self) -> u64 {
        self.exec.output_len()
    }

    #[must_use]
    pub fn elapsed_seconds(&self) -> f64 {
        self.started_at.elapsed().as_secs_f64()
    }

    /// The terminal result via the non-consuming promise peek; `None` while the
    /// command is still running. `is_finished() == true` implies `Some`.
    #[must_use]
    pub fn terminal_result(
        &self,
    ) -> Option<Result<CommandTerminalResult, NamespaceExecutionError>> {
        self.exec.resolved()
    }

    /// Block up to `timeout` for completion — the yield loop's condvar wait.
    pub fn wait_timeout(&self, timeout: Duration) -> bool {
        self.exec.wait_timeout(timeout)
    }

    /// A lock-free waiter cloned from this command's promise, so the yield loop can
    /// block on completion without holding the registry lock.
    #[must_use]
    pub fn completion(&self) -> Arc<dyn CompletionWaiter> {
        self.exec.completion()
    }

    #[must_use]
    pub fn take_snapshot_offset(&self) -> u64 {
        self.next_snapshot_offset.load(Ordering::Acquire)
    }

    pub fn advance_snapshot_offset(&self, next: u64) {
        self.next_snapshot_offset.store(next, Ordering::Release);
    }

    /// A transcript window over the file, infallible (empty when the file is
    /// missing) — the running/terminal yield read.
    #[must_use]
    pub fn transcript_window(&self, start: u64, limit: usize) -> CommandTranscriptWindow {
        transcript_window(self.transcript_path.as_deref(), start, limit)
    }

    /// A transcript window that fails when the retained transcript is missing —
    /// the terminal `read_command_lines` read.
    pub fn required_transcript_window(
        &self,
        start: u64,
        limit: usize,
    ) -> Result<CommandTranscriptWindow, String> {
        required_transcript_window(self.transcript_path.as_deref(), start, limit)
    }
}
