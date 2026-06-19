use std::path::PathBuf;
use std::time::SystemTime;

use crate::workspace_crate::{BaseRevision, WorkspaceHandle, WorkspaceId};
use crate::workspace_session::WorkspaceSessionError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum WorkspaceLifecycleState {
    Active,
    Closing,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub enum WorkspaceRemountState {
    #[default]
    Active,
    RemountPending,
    RemountBlocked {
        reason: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceSessionHandler {
    pub workspace_session_id: WorkspaceId,
    pub handle: WorkspaceHandle,
    pub layer_stack_root: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct WorkspaceSession {
    pub workspace_session_id: WorkspaceId,
    pub handle: WorkspaceHandle,
    pub layer_stack_root: PathBuf,
    pub lifecycle_state: WorkspaceLifecycleState,
    pub remount_state: WorkspaceRemountState,
    pub created_at: SystemTime,
    pub last_activity: SystemTime,
}

impl WorkspaceSession {
    pub(crate) fn from_handle(handle: WorkspaceHandle, layer_stack_root: PathBuf) -> Self {
        let now = SystemTime::now();
        Self {
            workspace_session_id: handle.id.clone(),
            layer_stack_root,
            handle,
            lifecycle_state: WorkspaceLifecycleState::Active,
            remount_state: WorkspaceRemountState::Active,
            created_at: now,
            last_activity: now,
        }
    }

    pub(crate) fn handler(&self) -> WorkspaceSessionHandler {
        WorkspaceSessionHandler {
            workspace_session_id: self.workspace_session_id.clone(),
            handle: self.handle.clone(),
            layer_stack_root: self.layer_stack_root.clone(),
        }
    }

    pub(crate) fn ensure_active(&self) -> Result<(), WorkspaceSessionError> {
        match self.lifecycle_state {
            WorkspaceLifecycleState::Active => Ok(()),
            WorkspaceLifecycleState::Closing => Err(WorkspaceSessionError::Closing {
                workspace_session_id: self.workspace_session_id.clone(),
            }),
        }
    }

    pub(crate) fn ensure_remount_not_pending(&self) -> Result<(), WorkspaceSessionError> {
        if matches!(self.remount_state, WorkspaceRemountState::RemountPending) {
            return Err(WorkspaceSessionError::RemountAlreadyPending {
                workspace_session_id: self.workspace_session_id.clone(),
            });
        }
        Ok(())
    }

    pub(crate) fn active_handle(&self) -> Result<WorkspaceHandle, WorkspaceSessionError> {
        self.ensure_active()?;
        self.ensure_remount_not_pending()?;
        Ok(self.handle.clone())
    }

    pub(crate) fn mark_closing(&mut self) -> Result<WorkspaceHandle, WorkspaceSessionError> {
        self.ensure_active()?;
        self.ensure_remount_not_pending()?;
        self.lifecycle_state = WorkspaceLifecycleState::Closing;
        self.last_activity = SystemTime::now();
        Ok(self.handle.clone())
    }

    pub(crate) fn mark_active(&mut self) {
        self.lifecycle_state = WorkspaceLifecycleState::Active;
        self.last_activity = SystemTime::now();
    }

    pub(crate) fn begin_remount(
        &mut self,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        self.ensure_active()?;
        if matches!(self.remount_state, WorkspaceRemountState::RemountPending) {
            return Err(WorkspaceSessionError::RemountAlreadyPending {
                workspace_session_id: self.workspace_session_id.clone(),
            });
        }
        self.remount_state = WorkspaceRemountState::RemountPending;
        self.last_activity = SystemTime::now();
        Ok(self.handler())
    }

    pub(crate) fn finish_remount(&mut self) -> Result<(), WorkspaceSessionError> {
        self.ensure_active()?;
        if !matches!(
            self.remount_state,
            WorkspaceRemountState::RemountPending | WorkspaceRemountState::RemountBlocked { .. }
        ) {
            return Err(WorkspaceSessionError::RemountNotPending {
                workspace_session_id: self.workspace_session_id.clone(),
            });
        }
        self.remount_state = WorkspaceRemountState::Active;
        self.last_activity = SystemTime::now();
        Ok(())
    }

    pub(crate) fn block_remount(&mut self, reason: String) -> Result<(), WorkspaceSessionError> {
        self.ensure_active()?;
        if !matches!(self.remount_state, WorkspaceRemountState::RemountPending) {
            return Err(WorkspaceSessionError::RemountNotPending {
                workspace_session_id: self.workspace_session_id.clone(),
            });
        }
        self.remount_state = WorkspaceRemountState::RemountBlocked { reason };
        self.last_activity = SystemTime::now();
        Ok(())
    }

    pub(crate) fn refresh_after_capture(&mut self, base_revision: BaseRevision) {
        self.handle.base_revision = base_revision;
        self.handle.snapshot.manifest_version = self.handle.base_revision.version;
        self.handle.snapshot.root_hash = self.handle.base_revision.root_hash.clone();
        self.last_activity = SystemTime::now();
    }

    pub(crate) fn refresh_from_handle(
        &mut self,
        handle: WorkspaceHandle,
    ) -> Result<(), WorkspaceSessionError> {
        if handle.id != self.workspace_session_id {
            return Err(WorkspaceSessionError::RemountWorkspaceSessionIdMismatch {
                expected: self.workspace_session_id.clone(),
                actual: handle.id,
            });
        }

        self.handle = handle;
        self.last_activity = SystemTime::now();
        Ok(())
    }
}
