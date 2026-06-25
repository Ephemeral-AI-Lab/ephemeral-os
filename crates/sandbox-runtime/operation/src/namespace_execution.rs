use std::collections::{HashSet, VecDeque};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use sandbox_runtime_namespace_execution::NamespaceExecutionError;

use crate::command::CommandTerminalResult;
use crate::workspace_crate::WorkspaceSessionId;

pub use sandbox_runtime_namespace_execution::{
    NamespaceExecutionId, NamespaceExecutionTerminalStatus,
};

const DEFAULT_MAX_PENDING_PROJECTION: usize = 256;
const DEFAULT_MAX_RECENT_PROJECTED: usize = 256;
const DEFAULT_MAX_PARTIAL_ERRORS: usize = 32;
const MAX_ERROR_FIELD_BYTES: usize = 4096;

/// A pure completed-projection buffer: it ingests fully-built terminal
/// `NamespaceExecutionRecord`s (via `record_completed`) and hands them to the
/// daemon projection through `drain`/`ack`. Liveness is owned by the engine
/// registry, not here.
#[derive(Debug)]
pub struct NamespaceExecutionLedger {
    inner: Mutex<NamespaceExecutionState>,
    max_pending_projection: usize,
    max_recent_projected: usize,
    max_partial_errors: usize,
}

#[derive(Debug)]
struct NamespaceExecutionState {
    pending_projection: VecDeque<NamespaceExecutionRecord>,
    recent_projected: VecDeque<NamespaceExecutionRecord>,
    partial_errors: VecDeque<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct NamespaceExecutionRecord {
    pub namespace_execution_id: NamespaceExecutionId,
    pub workspace_session_id: WorkspaceSessionId,
    pub operation_name: String,
    pub origin_request_id: Option<String>,
    pub started_at_unix_ms: i64,
    pub finished_at_unix_ms: Option<i64>,
    pub duration_ms: Option<f64>,
    pub terminal_status: Option<NamespaceExecutionTerminalStatus>,
    pub exit_code: Option<i64>,
    pub error_kind: Option<String>,
    pub error_message: Option<String>,
}

/// The command-owned provenance for a completed record: stamped once at
/// `exec_command` and reused by the `on_complete` closure.
pub struct CompletedNamespaceExecutionMeta {
    pub namespace_execution_id: NamespaceExecutionId,
    pub workspace_session_id: WorkspaceSessionId,
    pub operation_name: String,
    pub origin_request_id: Option<String>,
    pub started_at_unix_ms: i64,
}

impl NamespaceExecutionRecord {
    #[must_use]
    pub fn completed(
        meta: CompletedNamespaceExecutionMeta,
        result: &Result<CommandTerminalResult, NamespaceExecutionError>,
    ) -> Self {
        let finished_at_unix_ms = unix_ms();
        let (terminal_status, exit_code, error_message) = match result {
            Ok(terminal) => (terminal.status, Some(terminal.exit_code), None),
            Err(error) => (
                NamespaceExecutionTerminalStatus::Error,
                None,
                Some(bound_error_field(error.to_string())),
            ),
        };
        Self {
            namespace_execution_id: meta.namespace_execution_id,
            workspace_session_id: meta.workspace_session_id,
            operation_name: meta.operation_name,
            origin_request_id: meta.origin_request_id,
            started_at_unix_ms: meta.started_at_unix_ms,
            finished_at_unix_ms: Some(finished_at_unix_ms),
            duration_ms: Some(duration_ms(meta.started_at_unix_ms, finished_at_unix_ms)),
            terminal_status: Some(terminal_status),
            exit_code,
            error_kind: None,
            error_message,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct RuntimeNamespaceExecutionSnapshot {
    pub namespace_execution_id: NamespaceExecutionId,
    pub workspace_session_id: WorkspaceSessionId,
    pub operation_name: String,
}

impl NamespaceExecutionLedger {
    #[must_use]
    pub fn new() -> Self {
        Self::with_limits(
            DEFAULT_MAX_PENDING_PROJECTION,
            DEFAULT_MAX_RECENT_PROJECTED,
            DEFAULT_MAX_PARTIAL_ERRORS,
        )
    }

    #[must_use]
    pub fn with_limits(
        max_pending_projection: usize,
        max_recent_projected: usize,
        max_partial_errors: usize,
    ) -> Self {
        Self {
            inner: Mutex::new(NamespaceExecutionState {
                pending_projection: VecDeque::new(),
                recent_projected: VecDeque::new(),
                partial_errors: VecDeque::new(),
            }),
            max_pending_projection,
            max_recent_projected,
            max_partial_errors,
        }
    }

    pub fn record_completed(&self, record: NamespaceExecutionRecord) -> Result<(), String> {
        let mut state = self.lock_state()?;
        if self.max_pending_projection > 0 {
            while state.pending_projection.len() >= self.max_pending_projection {
                let Some(dropped) = state.pending_projection.pop_front() else {
                    break;
                };
                push_partial_error(
                    &mut state,
                    self.max_partial_errors,
                    format!(
                        "dropped namespace execution {} before projection acknowledgement",
                        dropped.namespace_execution_id.0
                    ),
                );
                push_recent_projected(&mut state, self.max_recent_projected, dropped);
            }
            state.pending_projection.push_back(record);
        } else {
            push_partial_error(
                &mut state,
                self.max_partial_errors,
                format!(
                    "dropped namespace execution {} before projection acknowledgement",
                    record.namespace_execution_id.0
                ),
            );
            push_recent_projected(&mut state, self.max_recent_projected, record);
        }
        Ok(())
    }

    pub fn drain_completed_namespace_executions(
        &self,
        limit: usize,
    ) -> Result<Vec<NamespaceExecutionRecord>, String> {
        let state = self.lock_state()?;
        Ok(state
            .pending_projection
            .iter()
            .take(limit)
            .cloned()
            .collect())
    }

    pub fn ack_completed_namespace_executions(
        &self,
        namespace_execution_ids: &[NamespaceExecutionId],
    ) -> Result<(), String> {
        let mut state = self.lock_state()?;
        let ids = namespace_execution_ids.iter().collect::<HashSet<_>>();
        let mut kept = VecDeque::new();
        let mut acked = Vec::new();
        while let Some(record) = state.pending_projection.pop_front() {
            if ids.contains(&record.namespace_execution_id) {
                acked.push(record);
            } else {
                kept.push_back(record);
            }
        }
        state.pending_projection = kept;
        for record in acked {
            push_recent_projected(&mut state, self.max_recent_projected, record);
        }
        Ok(())
    }

    pub fn drain_partial_errors(&self) -> Result<Vec<String>, String> {
        let mut state = self.lock_state()?;
        Ok(state.partial_errors.drain(..).collect())
    }

    fn lock_state(&self) -> Result<std::sync::MutexGuard<'_, NamespaceExecutionState>, String> {
        self.inner
            .lock()
            .map_err(|_| "namespace execution store lock is poisoned".to_owned())
    }
}

impl Default for NamespaceExecutionLedger {
    fn default() -> Self {
        Self::new()
    }
}

fn push_recent_projected(
    state: &mut NamespaceExecutionState,
    max_recent_projected: usize,
    record: NamespaceExecutionRecord,
) {
    if max_recent_projected == 0 {
        return;
    }
    while state.recent_projected.len() >= max_recent_projected {
        let _ = state.recent_projected.pop_front();
    }
    state.recent_projected.push_back(record);
}

fn push_partial_error(
    state: &mut NamespaceExecutionState,
    max_partial_errors: usize,
    error: String,
) {
    if max_partial_errors == 0 {
        return;
    }
    while state.partial_errors.len() >= max_partial_errors {
        let _ = state.partial_errors.pop_front();
    }
    state.partial_errors.push_back(bound_error_field(error));
}

fn bound_error_field(value: String) -> String {
    if value.len() <= MAX_ERROR_FIELD_BYTES {
        return value;
    }
    let mut end = MAX_ERROR_FIELD_BYTES;
    while !value.is_char_boundary(end) {
        end = end.saturating_sub(1);
    }
    value[..end].to_owned()
}

fn duration_ms(started_at_unix_ms: i64, finished_at_unix_ms: i64) -> f64 {
    finished_at_unix_ms
        .saturating_sub(started_at_unix_ms)
        .max(0) as f64
}

pub(crate) fn unix_ms() -> i64 {
    i64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis(),
    )
    .unwrap_or(i64::MAX)
}
