use std::cell::Cell;
use std::path::PathBuf;
use std::sync::{Arc, OnceLock};
use std::time::Instant;

use sandbox_runtime_namespace_execution::{
    required_transcript_window, transcript_window, CommandTranscriptWindow, InteractiveExecution,
};

use super::contract::CommandTerminalResult;
use crate::workspace_crate::WorkspaceSessionId;
use crate::workspace_session::FinalizeOutcome;

/// The per-execution value the engine registry holds for a command. The engine
/// forwards (`is_finished`/`output_len`/`completion`/`write_stdin`/`cancel`/
/// `resolved`) are reached through `value.exec`; this type only adds what the
/// command layer owns beyond `InteractiveExecution`: the transcript window, the
/// elapsed-time clocks, the streaming snapshot offset, and the finalize-outcome
/// slot set at attach (§2.5). Dropping the value — retention eviction or engine
/// teardown — removes the command's scratch directory alongside the pty fd the
/// wrapped execution holds.
pub struct CommandExecValue {
    pub(crate) exec: InteractiveExecution<CommandTerminalResult>,
    transcript_path: PathBuf,
    pub(crate) workspace_session_id: WorkspaceSessionId,
    started_at: Instant,
    pub(crate) operation_name: &'static str,
    pub(crate) command: String,
    next_snapshot_offset: Cell<u64>,
    pub(crate) finalize_outcome: Arc<OnceLock<FinalizeOutcome>>,
    max_transcript_window_bytes: u64,
}

impl CommandExecValue {
    #[must_use]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        exec: InteractiveExecution<CommandTerminalResult>,
        transcript_path: PathBuf,
        workspace_session_id: WorkspaceSessionId,
        started_at: Instant,
        operation_name: &'static str,
        command: String,
        finalize_outcome: Arc<OnceLock<FinalizeOutcome>>,
        max_transcript_window_bytes: u64,
    ) -> Self {
        Self {
            exec,
            transcript_path,
            workspace_session_id,
            started_at,
            operation_name,
            command,
            next_snapshot_offset: Cell::new(0),
            finalize_outcome,
            max_transcript_window_bytes,
        }
    }

    #[must_use]
    pub fn elapsed_seconds(&self) -> f64 {
        self.started_at.elapsed().as_secs_f64()
    }

    #[must_use]
    pub fn take_snapshot_offset(&self) -> u64 {
        self.next_snapshot_offset.get()
    }

    pub fn advance_snapshot_offset(&self, next: u64) {
        self.next_snapshot_offset.set(next);
    }

    #[must_use]
    pub fn transcript_window(&self, start: u64, limit: usize) -> CommandTranscriptWindow {
        transcript_window(
            Some(&self.transcript_path),
            start,
            limit,
            self.max_transcript_window_bytes,
        )
    }

    pub fn required_transcript_window(
        &self,
        start: u64,
        limit: usize,
    ) -> Result<CommandTranscriptWindow, String> {
        required_transcript_window(
            Some(&self.transcript_path),
            start,
            limit,
            self.max_transcript_window_bytes,
        )
    }
}

impl Drop for CommandExecValue {
    fn drop(&mut self) {
        if let Some(command_dir) = self.transcript_path.parent() {
            let _ = std::fs::remove_dir_all(command_dir);
        }
    }
}
