use eos_types::{WorkflowId, WorkflowSessionId};

use super::super::{BackgroundSession, BackgroundSessionStatus};

/// One delegated workflow tracked as background work for the owning agent run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(in crate::background) struct WorkflowSession {
    id: WorkflowSessionId,
    workflow_id: WorkflowId,
    status: BackgroundSessionStatus,
}

impl WorkflowSession {
    pub(super) fn running(id: WorkflowSessionId, workflow_id: WorkflowId) -> Self {
        Self {
            id,
            workflow_id,
            status: BackgroundSessionStatus::Running,
        }
    }

    pub(super) fn workflow_id(&self) -> &WorkflowId {
        &self.workflow_id
    }

    pub(super) const fn status(&self) -> BackgroundSessionStatus {
        self.status
    }

    pub(super) fn cancel(&mut self) -> bool {
        if !matches!(self.status, BackgroundSessionStatus::Running) {
            return false;
        }
        self.status = BackgroundSessionStatus::Cancelled;
        true
    }

    pub(super) fn settle_running(&mut self, status: BackgroundSessionStatus) -> bool {
        if !matches!(self.status, BackgroundSessionStatus::Running) {
            return false;
        }
        self.status = status;
        true
    }
}

impl BackgroundSession for WorkflowSession {
    type Id = WorkflowSessionId;

    fn id(&self) -> &Self::Id {
        &self.id
    }
}
