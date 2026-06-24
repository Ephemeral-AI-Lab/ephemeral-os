
use std::io;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Instant;

use sandbox_runtime_namespace_execution::{
    CompletionWaiter, InteractiveExecution, NamespaceExecutionError, NamespaceExecutionId,
};
use sandbox_runtime_workspace::WorkspaceSessionId;

use crate::contract::CommandTerminalResult;
use crate::transcript_rows::{
    required_transcript_window, transcript_window, CommandTranscriptWindow,
};

pub struct CommandExecution {
    exec: InteractiveExecution<CommandTerminalResult>,
    transcript_path: Option<PathBuf>,
    workspace_session_id: WorkspaceSessionId,
    started_at: Instant,
    next_snapshot_offset: AtomicU64,
}

impl CommandExecution {
    #[must_use]
    pub fn new(
        exec: InteractiveExecution<CommandTerminalResult>,
        transcript_path: Option<PathBuf>,
        workspace_session_id: WorkspaceSessionId,
        started_at: Instant,
    ) -> Self {
        Self {
            exec,
            transcript_path,
            workspace_session_id,
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
    pub fn pgid(&self) -> Option<i32> {
        self.exec.pgid()
    }

    pub fn write_stdin(&self, bytes: &[u8]) -> io::Result<()> {
        self.exec.write_stdin(bytes)
    }

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

    #[must_use]
    pub fn terminal_result(
        &self,
    ) -> Option<Result<CommandTerminalResult, NamespaceExecutionError>> {
        self.exec.resolved()
    }

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

    #[must_use]
    pub fn transcript_window(&self, start: u64, limit: usize) -> CommandTranscriptWindow {
        transcript_window(self.transcript_path.as_deref(), start, limit)
    }

    pub fn required_transcript_window(
        &self,
        start: u64,
        limit: usize,
    ) -> Result<CommandTranscriptWindow, String> {
        required_transcript_window(self.transcript_path.as_deref(), start, limit)
    }
}
