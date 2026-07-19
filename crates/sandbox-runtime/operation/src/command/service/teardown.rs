use std::fmt;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::Arc;
use std::time::{Duration, Instant};

use sandbox_runtime_namespace_execution::{CompletionWaiter, NamespaceExecutionId};

use crate::workspace_crate::WorkspaceSessionId;

pub(crate) const COMMAND_JOIN_TIMEOUT: Duration = Duration::from_secs(1);

pub(crate) struct CommandTeardownTarget {
    pub(crate) owner: WorkspaceSessionId,
    pub(crate) cancel: Arc<dyn Fn() + Send + Sync>,
    pub(crate) completion: Arc<dyn CompletionWaiter>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum CommandTeardownFailure {
    MissingExecutionHandle {
        command_id: NamespaceExecutionId,
    },
    WorkspaceMismatch {
        command_id: NamespaceExecutionId,
        actual_workspace_id: WorkspaceSessionId,
        expected_workspace_id: WorkspaceSessionId,
    },
    CancellationPanicked {
        command_id: NamespaceExecutionId,
    },
    JoinTimedOut {
        command_id: NamespaceExecutionId,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CommandTeardownError {
    pub(crate) failures: Vec<CommandTeardownFailure>,
}

impl fmt::Display for CommandTeardownError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "command teardown failed")?;
        for failure in &self.failures {
            write!(formatter, "; {failure}")?;
        }
        Ok(())
    }
}

impl fmt::Display for CommandTeardownFailure {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingExecutionHandle { command_id } => write!(
                formatter,
                "missing_execution_handle(command_id={})",
                command_id.0
            ),
            Self::WorkspaceMismatch {
                command_id,
                actual_workspace_id,
                expected_workspace_id,
            } => write!(
                formatter,
                "workspace_mismatch(command_id={},actual_workspace_id={},expected_workspace_id={})",
                command_id.0, actual_workspace_id.0, expected_workspace_id.0
            ),
            Self::CancellationPanicked { command_id } => write!(
                formatter,
                "cancellation_panicked(command_id={})",
                command_id.0
            ),
            Self::JoinTimedOut { command_id } => {
                write!(
                    formatter,
                    "timed out joining command {} after cancellation",
                    command_id.0
                )
            }
        }
    }
}

pub(crate) fn cancel_and_join_commands(
    workspace_session_id: &WorkspaceSessionId,
    command_ids: &[NamespaceExecutionId],
    timeout: Duration,
    mut resolve: impl FnMut(&NamespaceExecutionId) -> Option<CommandTeardownTarget>,
) -> Result<(), CommandTeardownError> {
    let mut failures = Vec::new();
    let mut eligible = Vec::with_capacity(command_ids.len());
    for command_id in command_ids {
        let Some(target) = resolve(command_id) else {
            failures.push(CommandTeardownFailure::MissingExecutionHandle {
                command_id: command_id.clone(),
            });
            continue;
        };
        if target.owner != *workspace_session_id {
            failures.push(CommandTeardownFailure::WorkspaceMismatch {
                command_id: command_id.clone(),
                actual_workspace_id: target.owner,
                expected_workspace_id: workspace_session_id.clone(),
            });
            continue;
        }
        eligible.push((command_id.clone(), target.cancel, target.completion));
    }

    let deadline = Instant::now() + timeout;
    for (command_id, cancel, _) in &eligible {
        if catch_unwind(AssertUnwindSafe(|| cancel())).is_err() {
            failures.push(CommandTeardownFailure::CancellationPanicked {
                command_id: command_id.clone(),
            });
        }
    }
    for (command_id, _, completion) in eligible {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if !completion.wait_timeout(remaining) {
            failures.push(CommandTeardownFailure::JoinTimedOut { command_id });
        }
    }

    if failures.is_empty() {
        Ok(())
    } else {
        Err(CommandTeardownError { failures })
    }
}
