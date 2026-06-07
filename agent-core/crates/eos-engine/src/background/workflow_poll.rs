//! [`WorkflowCompletionPoller`] (spec §9.2/§13.1) — the per-agent-run poll that
//! pushes one `[BACKGROUND COMPLETED]` notification when a delegated workflow this
//! run launched reaches a terminal state. It is the workflow counterpart of the
//! command-completion heartbeat: subagent and command-session completions push
//! directly, but a delegated workflow runs as separate Tasks, so the delegating
//! run learns of completion by polling [`WorkflowControlPort::status`].
//!
//! Reference-cycle rule (mirrors §8.3): the poll task captures only a `Weak` to
//! the supervisor ledger and a `Weak` to the late-bound workflow-control cell —
//! never a strong ref that transitively owns its own `JoinHandle`. The
//! `JoinHandle` lives on the supervisor runtime, so the runtime drop aborts it.
//!
//! Status parsing is coupled to `WorkflowControlAdapter::status`, which renders
//! the typed `WorkflowStatus` as `"… is Open. …"` / `"… is Succeeded. …"` /
//! `"… is Failed. …"` / `"… is Cancelled. …"`. A non-terminal or unparsed string
//! is treated as still-running; the `check_workflow_status` tool remains the
//! authoritative pull path, so a missed parse only delays — never corrupts — the
//! push.

use std::sync::{Arc, OnceLock, Weak};
use std::time::Duration;

use eos_tools::WorkflowControlPort;
use tokio::sync::Mutex;
use tokio::task::JoinHandle;
use tokio::time::sleep;

use super::lanes::BackgroundTaskStatus;
use super::notifications::{BackgroundCompletion, BackgroundNotificationEmitter};
use super::supervisor::BackgroundTaskSupervisor;

/// The late-bound workflow-control cell shared with `eos-runtime`.
pub(super) type WorkflowControlCell = Arc<OnceLock<Arc<dyn WorkflowControlPort>>>;

/// Map a `WorkflowControlPort::status` rendering to a terminal status, or `None`
/// while the workflow is still open / the string is not a recognized terminal.
fn terminal_status(status_text: &str) -> Option<BackgroundTaskStatus> {
    if status_text.contains("is Succeeded.") {
        Some(BackgroundTaskStatus::Completed)
    } else if status_text.contains("is Failed.") {
        Some(BackgroundTaskStatus::Failed)
    } else if status_text.contains("is Cancelled.") {
        Some(BackgroundTaskStatus::Cancelled)
    } else {
        None
    }
}

/// RAII poller: `Drop` aborts the task when the supervisor runtime drops.
pub(super) struct WorkflowCompletionPoller {
    join: JoinHandle<()>,
}

impl Drop for WorkflowCompletionPoller {
    fn drop(&mut self) {
        self.join.abort();
    }
}

impl WorkflowCompletionPoller {
    /// Spawn the poll. `inner` and `control_cell` are `Weak` (cycle rule); the
    /// task exits when either is gone. Must be called within a Tokio runtime.
    pub(super) fn spawn(
        inner: Weak<Mutex<BackgroundTaskSupervisor>>,
        control_cell: Weak<OnceLock<Arc<dyn WorkflowControlPort>>>,
        notifications: BackgroundNotificationEmitter,
        interval: Duration,
    ) -> Self {
        let join = tokio::spawn(async move {
            loop {
                sleep(interval).await;
                let Some(inner) = inner.upgrade() else {
                    return;
                };
                let Some(cell) = control_cell.upgrade() else {
                    return;
                };
                let Some(workflow_control) = cell.get().cloned() else {
                    continue; // control not bound yet
                };
                drop(cell);
                let running = { inner.lock().await.workflows.running_handles() };
                for handle in running {
                    let status_text = match workflow_control
                        .status(&handle.workflow_id, Some(&handle.workflow_task_id))
                        .await
                    {
                        Ok(text) => text,
                        Err(_) => continue, // transient; retried next tick
                    };
                    let Some(terminal) = terminal_status(&status_text) else {
                        continue;
                    };
                    let settled = {
                        inner
                            .lock()
                            .await
                            .workflows
                            .settle_running(&handle.workflow_task_id, terminal)
                    };
                    if let Some(settled) = settled {
                        let _ = notifications
                            .emit(BackgroundCompletion::Workflow {
                                workflow_task_id: settled.workflow_task_id,
                                workflow_id: settled.workflow_id,
                                status: terminal,
                            })
                            .await;
                    }
                }
            }
        });
        Self { join }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used)]

    use async_trait::async_trait;
    use eos_state::TaskId;
    use eos_tools::{OutstandingWorkflow, StartedWorkflowHandle, ToolError};
    use eos_types::{AgentRunId, WorkflowId, WorkflowSessionId};
    use tokio::time::{sleep, timeout};

    use crate::NotificationService;

    use super::*;

    #[derive(Debug)]
    struct AlwaysSucceededControl;

    impl eos_tools::ports::Sealed for AlwaysSucceededControl {}

    #[async_trait]
    impl WorkflowControlPort for AlwaysSucceededControl {
        async fn start(
            &self,
            _parent_task_id: &TaskId,
            _agent_run_id: &AgentRunId,
            _workflow_goal: &str,
        ) -> Result<StartedWorkflowHandle, ToolError> {
            unreachable!("not used")
        }

        async fn status(
            &self,
            workflow_id: &WorkflowId,
            workflow_task_id: Option<&WorkflowSessionId>,
        ) -> Result<String, ToolError> {
            let handle = workflow_task_id.map_or("?", WorkflowSessionId::as_str);
            Ok(format!(
                "Workflow {workflow_id} ({handle}) is Succeeded. Goal: x"
            ))
        }

        async fn cancel(
            &self,
            _workflow_task_id: &WorkflowSessionId,
            _reason: &str,
        ) -> Result<String, ToolError> {
            unreachable!("not used")
        }

        async fn find_outstanding(
            &self,
            _parent_task_id: &TaskId,
            _agent_run_id: &AgentRunId,
        ) -> Result<Vec<OutstandingWorkflow>, ToolError> {
            Ok(Vec::new())
        }

        async fn workflow_depth(&self, _workflow_id: &WorkflowId) -> Result<u32, ToolError> {
            Ok(1)
        }
    }

    // §9.2/§13.1/§19: a delegated workflow reaching a terminal state pushes exactly
    // one [BACKGROUND COMPLETED] into the owning run's own notifier, and the record
    // is settled so it is not re-polled.
    #[tokio::test]
    async fn workflow_completion_pushes_one_notification() {
        let inner = Arc::new(Mutex::new(BackgroundTaskSupervisor::new()));
        inner
            .lock()
            .await
            .workflows
            .register(&StartedWorkflowHandle {
                workflow_id: WorkflowId::new_v4(),
                workflow_task_id: "wf_1".parse().expect("workflow handle"),
            });
        let cell: Arc<OnceLock<Arc<dyn WorkflowControlPort>>> = Arc::new(OnceLock::new());
        let _ = cell.set(Arc::new(AlwaysSucceededControl));
        let notifier = NotificationService::new();
        let poller = WorkflowCompletionPoller::spawn(
            Arc::downgrade(&inner),
            Arc::downgrade(&cell),
            BackgroundNotificationEmitter::new(notifier.clone()),
            Duration::from_millis(1),
        );

        let notifications = timeout(Duration::from_millis(200), async {
            loop {
                let drained = notifier.drain().await;
                if !drained.is_empty() {
                    break drained;
                }
                sleep(Duration::from_millis(2)).await;
            }
        })
        .await
        .expect("workflow completion notification");
        // Give any extra ticks a chance to (wrongly) re-emit before asserting once.
        sleep(Duration::from_millis(10)).await;
        let extra = notifier.drain().await;
        drop(poller);

        assert_eq!(notifications.len(), 1);
        assert!(notifications[0]
            .message
            .contains("[BACKGROUND COMPLETED] workflow_task_id=wf_1"));
        assert!(notifications[0].message.contains("status=completed"));
        assert!(extra.is_empty(), "settled workflow must not re-emit");
        assert!(
            inner.lock().await.workflows.running_handles().is_empty(),
            "the workflow record is settled terminal"
        );
    }

    #[test]
    fn terminal_status_parses_renderings() {
        assert_eq!(
            terminal_status("Workflow w1 (wf_1) is Succeeded. Goal: x"),
            Some(BackgroundTaskStatus::Completed)
        );
        assert_eq!(
            terminal_status("Workflow w1 (wf_1) is Failed. Goal: x"),
            Some(BackgroundTaskStatus::Failed)
        );
        assert_eq!(
            terminal_status("Workflow w1 (wf_1) is Cancelled. Goal: x"),
            Some(BackgroundTaskStatus::Cancelled)
        );
        assert_eq!(terminal_status("Workflow w1 (wf_1) is Open. Goal: x"), None);
        assert_eq!(terminal_status("Workflow wf_1 was not found."), None);
    }
}
