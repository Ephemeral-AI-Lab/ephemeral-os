//! [`WorkflowLane`] (spec §9.2) — the per-agent-run delegated-workflow ledger.
//! The lane stores the public [`WorkflowHandle`] and supervisor status; the
//! durable workflow lifecycle stays owned by `eos-workflow` /
//! [`WorkflowControlPort`](eos_tools::WorkflowControlPort). Cancellation
//! dispatches through `cancel_workflow`; this record owns running-work accounting
//! and parent-exit cleanup state.

use std::collections::HashMap;

use eos_tools::StartedWorkflowHandle;
use eos_types::{WorkflowId, WorkflowSessionId};

use super::BackgroundTaskStatus;

/// The first-class handle for one tracked delegated workflow (spec §9.2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkflowHandle {
    /// Agent-facing workflow handle id.
    pub workflow_task_id: WorkflowSessionId,
    /// The persisted workflow id.
    pub workflow_id: WorkflowId,
}

/// One delegated workflow tracked as background work.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkflowBackgroundRecord {
    /// The workflow handle.
    pub handle: WorkflowHandle,
    /// Current supervisor status.
    pub status: BackgroundTaskStatus,
}

impl WorkflowBackgroundRecord {
    /// Cancel this workflow record in-place. Persisted state cancellation is
    /// handled by [`WorkflowControlPort`](eos_tools::WorkflowControlPort).
    fn cancel(&mut self) -> bool {
        if !matches!(self.status, BackgroundTaskStatus::Running) {
            return false;
        }
        self.status = BackgroundTaskStatus::Cancelled;
        true
    }
}

/// The per-agent-run delegated-workflow ledger.
#[derive(Debug, Default)]
pub struct WorkflowLane {
    records: HashMap<WorkflowSessionId, WorkflowBackgroundRecord>,
}

impl WorkflowLane {
    /// Track a workflow this run just delegated.
    pub(crate) fn register(&mut self, workflow: &StartedWorkflowHandle) {
        self.records.insert(
            workflow.workflow_task_id.clone(),
            WorkflowBackgroundRecord {
                handle: WorkflowHandle {
                    workflow_task_id: workflow.workflow_task_id.clone(),
                    workflow_id: workflow.workflow_id.clone(),
                },
                status: BackgroundTaskStatus::Running,
            },
        );
    }

    /// Mark one tracked workflow cancelled in the ledger.
    pub(crate) fn cancel_record(&mut self, workflow_task_id: &WorkflowSessionId) -> bool {
        self.records
            .get_mut(workflow_task_id)
            .is_some_and(WorkflowBackgroundRecord::cancel)
    }

    /// Settle one running workflow to a terminal status (the completion-poll path).
    /// Only a still-`Running` record transitions, so a cancelled or already-settled
    /// record is never re-opened. Returns the handle on the fresh transition so the
    /// poll can emit exactly one completion.
    pub(crate) fn settle_running(
        &mut self,
        workflow_task_id: &WorkflowSessionId,
        status: BackgroundTaskStatus,
    ) -> Option<WorkflowHandle> {
        let record = self.records.get_mut(workflow_task_id)?;
        if matches!(record.status, BackgroundTaskStatus::Running) {
            record.status = status;
            Some(record.handle.clone())
        } else {
            None
        }
    }

    /// Running workflow handles (both ids), used by the completion poll to query
    /// status through [`WorkflowControlPort`](eos_tools::WorkflowControlPort).
    #[must_use]
    pub(crate) fn running_handles(&self) -> Vec<WorkflowHandle> {
        self.records
            .values()
            .filter(|record| matches!(record.status, BackgroundTaskStatus::Running))
            .map(|record| record.handle.clone())
            .collect()
    }

    /// Running workflow handle ids, used by parent-exit cleanup.
    #[must_use]
    pub(crate) fn running_ids(&self) -> Vec<WorkflowSessionId> {
        self.records
            .values()
            .filter(|record| matches!(record.status, BackgroundTaskStatus::Running))
            .map(|record| record.handle.workflow_task_id.clone())
            .collect()
    }

    /// Count still-running workflows.
    #[must_use]
    pub(crate) fn count_running(&self) -> usize {
        self.records
            .values()
            .filter(|record| matches!(record.status, BackgroundTaskStatus::Running))
            .count()
    }
}
