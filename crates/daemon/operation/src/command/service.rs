use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Instant;

use command::{CommandConfig, CommandError};
use layerstack::CommitOptions;
use trace::TraceRecord;
use workspace::IsolatedWorkspaceBinding;

#[cfg(test)]
use command::process::{CommandProcess, CommandProcessSpec};
#[cfg(test)]
use command::{ReadCommandProgress, WriteStdin};
#[cfg(test)]
use serde_json::json;

#[cfg(test)]
use super::contract::CommandCompletion;
use super::contract::{CommandResponse, CommandStatus};
use super::outcome::WorkspaceTimings;
use super::prepare::CommandPrepareError;
#[cfg(test)]
use super::prepare::PreparedCommand;
use super::registry::CommandRegistry;
#[cfg(test)]
use super::registry::{ActiveCommand, CommandTraceOrigin, IsolatedRun};
use super::trace::CommandTraceEvent;
#[cfg(test)]
use super::trace::{
    active_command_advance_trace_record, command_finalize_trace_record,
    command_process_wait_host_resource_stats_event, command_process_wait_resource_stats_event,
    command_process_wait_tree_resource_stats_events, CommandFinalizeTraceFacts,
};

mod exec;
mod io;
mod lifecycle;

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
    commit_options: CommitOptions,
    registry: Arc<CommandRegistry>,
    /// One "before" cgroup/process sample per live command, taken at spawn.
    /// Consumed exactly once: by the exec request sidecar when the command
    /// finalizes inside its own yield window, otherwise by whichever
    /// finalization path ends the command.
    before_resource_samples: Mutex<HashMap<String, WorkspaceTimings>>,
    /// Finalize trace records produced on foreground paths (progress read,
    /// cancel, drain, in-window exec); drained into the background spool by
    /// `advance_active_commands_once`.
    pending_finalize_records: Mutex<Vec<TraceRecord>>,
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandProgressTraceFacts {
    pub command_id: String,
    pub last_n_lines: usize,
    pub status: CommandStatus,
    pub source: &'static str,
    pub stdout_bytes: usize,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommandReadProgressOutcome {
    pub response: CommandResponse,
    pub trace: CommandProgressTraceFacts,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommandExecOutcome {
    pub response: CommandResponse,
    pub trace_events: Vec<CommandTraceEvent>,
}

#[derive(Debug)]
pub struct CommandExecError {
    error: CommandError,
    trace_events: Vec<CommandTraceEvent>,
}

impl CommandExecError {
    #[must_use]
    pub fn new(error: CommandError) -> Self {
        Self {
            error,
            trace_events: Vec::new(),
        }
    }

    #[must_use]
    pub fn with_trace_events(error: CommandError, trace_events: Vec<CommandTraceEvent>) -> Self {
        Self {
            error,
            trace_events,
        }
    }

    #[must_use]
    pub fn error(&self) -> &CommandError {
        &self.error
    }

    #[must_use]
    pub fn trace_events(&self) -> &[CommandTraceEvent] {
        &self.trace_events
    }

    #[must_use]
    pub fn into_error(self) -> CommandError {
        self.error
    }
}

impl From<CommandError> for CommandExecError {
    fn from(error: CommandError) -> Self {
        Self::new(error)
    }
}

impl std::fmt::Display for CommandExecError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.error.fmt(formatter)
    }
}

impl std::error::Error for CommandExecError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(&self.error)
    }
}

impl CommandOps {
    #[must_use]
    pub fn new(config: CommandConfig) -> Self {
        Self::with_commit_options(config, CommitOptions::default())
    }

    #[must_use]
    pub fn with_commit_options(config: CommandConfig, commit_options: CommitOptions) -> Self {
        Self {
            config,
            commit_options,
            registry: Arc::new(CommandRegistry::new()),
            before_resource_samples: Mutex::new(HashMap::new()),
            pending_finalize_records: Mutex::new(Vec::new()),
        }
    }

    #[must_use]
    pub fn config(&self) -> &CommandConfig {
        &self.config
    }

    #[must_use]
    pub const fn commit_options(&self) -> CommitOptions {
        self.commit_options
    }

    #[must_use]
    pub fn scratch_root(&self) -> PathBuf {
        self.config.scratch_root.clone()
    }

    pub(super) fn store_before_resource_sample(&self, command_id: &str, sample: WorkspaceTimings) {
        self.before_resource_samples
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .insert(command_id.to_owned(), sample);
    }

    pub(super) fn take_before_resource_sample(&self, command_id: &str) -> Option<WorkspaceTimings> {
        self.before_resource_samples
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .remove(command_id)
    }

    pub(super) fn push_pending_finalize_record(&self, record: TraceRecord) {
        self.pending_finalize_records
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .push(record);
    }

    pub(super) fn take_pending_finalize_records(&self) -> Vec<TraceRecord> {
        std::mem::take(
            &mut *self
                .pending_finalize_records
                .lock()
                .unwrap_or_else(PoisonError::into_inner),
        )
    }
}

pub(super) fn command_prepare_error(error: CommandPrepareError) -> CommandExecError {
    CommandExecError::with_trace_events(
        CommandError::Workspace(error.error.to_string()),
        error.trace_events,
    )
}

pub(super) fn elapsed_ms(started: Instant) -> u64 {
    u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX)
}

pub(super) fn progress_trace(
    command_id: &str,
    last_n_lines: usize,
    source: &'static str,
    response: &CommandResponse,
) -> CommandProgressTraceFacts {
    CommandProgressTraceFacts {
        command_id: command_id.to_owned(),
        last_n_lines,
        status: response.status,
        source,
        stdout_bytes: response.stdout.len(),
    }
}

pub(super) fn is_teardown_control(chars: &str) -> bool {
    matches!(chars, "\u{3}" | "\u{4}")
}

pub(super) fn contains_teardown_control(chars: &str) -> bool {
    chars.contains('\u{3}') || chars.contains('\u{4}')
}

#[cfg(test)]
#[path = "../../tests/command/service.rs"]
mod tests;
