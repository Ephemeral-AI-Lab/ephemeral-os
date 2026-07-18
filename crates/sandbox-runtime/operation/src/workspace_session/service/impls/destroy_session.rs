use std::path::PathBuf;
use std::sync::{Arc, PoisonError};

use sandbox_observability_telemetry::record::names;
use serde_json::json;

use crate::workspace_crate::{
    DestroyWorkspaceRequest, DestroyWorkspaceResult, WorkspaceError, WorkspaceHandle,
    WorkspaceSessionId,
};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::cgroup::cleanup_workspace_cgroup;
use super::super::core::DestroyFlight;
use super::super::model::WorkspaceSessionHandler;

/// The state a destroy needs, snapshotted under a brief `sessions` lock so the
/// lock is never held across the workspace teardown I/O (§2.3 hard rule).
pub(crate) struct DestroySnapshot {
    pub(crate) workspace_session_id: WorkspaceSessionId,
    pub(crate) handle: WorkspaceHandle,
    pub(crate) cgroup_path: Option<PathBuf>,
    pub(crate) workspace_destroy_result: Option<DestroyWorkspaceResult>,
    pub(crate) cgroup_cleanup_complete: bool,
}

impl WorkspaceSessionService {
    pub fn destroy_session(
        &self,
        handler: WorkspaceSessionHandler,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        let (flight, leader) = {
            let mut flights = self
                .destroy_flights
                .lock()
                .map_err(|_| WorkspaceSessionError::LockPoisoned)?;
            if let Some(flight) = flights.get(&handler.workspace_session_id) {
                (Arc::clone(flight), false)
            } else {
                let flight = Arc::new(DestroyFlight::new());
                flights.insert(handler.workspace_session_id.clone(), Arc::clone(&flight));
                (flight, true)
            }
        };

        if !leader {
            let result = flight
                .result
                .lock()
                .map_err(|_| WorkspaceSessionError::LockPoisoned)?;
            let result = flight
                .ready
                .wait_while(result, |result| result.is_none())
                .map_err(|_| WorkspaceSessionError::LockPoisoned)?;
            return result
                .as_ref()
                .expect("destroy leader always publishes a terminal result")
                .clone();
        }

        let result = self.obs().scope(names::WORKSPACE_SESSION_DESTROY, |_span| {
            let snapshot = self.snapshot_for_destroy(&handler.workspace_session_id)?;
            self.destroy_snapshot(snapshot, request)
        });
        if result.is_err() {
            if let Ok(mut sessions) = self.lock_sessions() {
                if let Some(session) = sessions.get_mut(&handler.workspace_session_id) {
                    session.finalization_state =
                        super::super::model::FinalizationState::FinalizeFailed;
                }
            }
        }
        {
            let mut slot = flight.result.lock().unwrap_or_else(PoisonError::into_inner);
            *slot = Some(result.clone());
            flight.ready.notify_all();
        }
        let mut flights = self
            .destroy_flights
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if flights
            .get(&handler.workspace_session_id)
            .is_some_and(|current| Arc::ptr_eq(current, &flight))
        {
            flights.remove(&handler.workspace_session_id);
        }
        result
    }

    pub(crate) fn snapshot_for_destroy(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> Result<DestroySnapshot, WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .get_mut(workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(workspace_session_id))?;
        session.finalization_state = super::super::model::FinalizationState::Finalizing;
        Ok(DestroySnapshot {
            workspace_session_id: session.workspace_session_id.clone(),
            handle: session.handle.clone(),
            cgroup_path: session.cgroup_path.clone(),
            workspace_destroy_result: session.workspace_destroy_result.clone(),
            cgroup_cleanup_complete: session.cgroup_cleanup_complete,
        })
    }

    pub(crate) fn destroy_snapshot(
        &self,
        snapshot: DestroySnapshot,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        let DestroySnapshot {
            workspace_session_id,
            handle,
            cgroup_path,
            workspace_destroy_result,
            cgroup_cleanup_complete,
        } = snapshot;
        let revision = handle.base_revision().version;
        let mut workspace_error: Option<WorkspaceError> = None;
        let workspace_result = match workspace_destroy_result {
            Some(result) => Some(result),
            None => match self.workspace().destroy_workspace(handle, request) {
                Ok(result) => Some(result),
                Err(error) => {
                    workspace_error = Some(error);
                    None
                }
            },
        };
        let mut cgroup_error = None;
        let cgroup_complete = if cgroup_cleanup_complete {
            true
        } else if let Some(cgroup_path) = &cgroup_path {
            match cleanup_workspace_cgroup(cgroup_path) {
                Ok(()) => true,
                Err(error) => {
                    cgroup_error = Some(error);
                    false
                }
            }
        } else {
            true
        };

        {
            let mut sessions = self.lock_sessions()?;
            if let Some(session) = sessions.get_mut(&workspace_session_id) {
                if let Some(result) = &workspace_result {
                    session.workspace_destroy_result = Some(result.clone());
                }
                session.cgroup_cleanup_complete = cgroup_complete;
            }
        }

        if let Some(error) = workspace_error {
            if cgroup_error.is_none() {
                return Err(WorkspaceSessionError::Workspace(error));
            }
            return Err(WorkspaceSessionError::TeardownIncomplete {
                workspace_session_id,
                failures: vec![
                    format!("workspace: {error}"),
                    format!(
                        "workload-cgroup: {}",
                        cgroup_error.expect("checked present")
                    ),
                ],
            });
        }
        if let Some(error) = cgroup_error {
            return Err(WorkspaceSessionError::TeardownIncomplete {
                workspace_session_id,
                failures: vec![format!("workload-cgroup: {error}")],
            });
        }

        let result = workspace_result.expect("no failures requires workspace teardown success");
        self.lock_sessions()?.remove(&workspace_session_id);
        self.drop_session_gate(&workspace_session_id);
        self.obs()
            .event(names::LEASE_RELEASED, json!({ "revision": revision }));
        Ok(result)
    }
}
