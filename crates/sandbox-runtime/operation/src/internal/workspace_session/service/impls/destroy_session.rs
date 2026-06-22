use std::time::Instant;

use crate::workspace_crate::{DestroyWorkspaceRequest, DestroyWorkspaceResult};
use crate::workspace_crate::{RuntimeMetricStatus, WorkspacePhase};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::model::WorkspaceSessionHandler;
use tracing::{field, Span};

impl WorkspaceSessionService {
    pub fn destroy_session(
        &self,
        handler: WorkspaceSessionHandler,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        let span = tracing::info_span!(
            "workspace.destroy_session",
            grace_requested = request.grace_s.is_some(),
            status = field::Empty,
            error_kind = field::Empty,
            cgroup_final_sample = field::Empty,
            cgroup_exists_after_destroy = field::Empty,
            lifetime_s = field::Empty,
            evicted_upperdir_bytes = field::Empty,
            lease_released = field::Empty,
            active_leases_after = field::Empty,
        );
        let _span_guard = span.enter();
        let started = Instant::now();
        let result = self.destroy_session_inner(handler, request);
        self.metrics().record_workspace_phase(
            WorkspacePhase::DestroySession,
            workspace_status(&result),
            started.elapsed(),
        );
        record_destroy_session_result(&span, &result);
        result
    }

    fn destroy_session_inner(
        &self,
        handler: WorkspaceSessionHandler,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .get(&handler.workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&handler.workspace_session_id))?;
        let handle = session.active_handle()?;
        let cgroup_path = handle.entry().ok().and_then(|entry| entry.cgroup_path);
        let cgroup_final_sample = self
            .cgroup_monitor()
            .session_final_sample_from_handle(&handle);

        match self.workspace().destroy_workspace(handle, request) {
            Ok(result) => {
                self.cgroup_monitor().record_session_final_sample(
                    &handler.workspace_session_id,
                    cgroup_final_sample,
                );
                self.cgroup_monitor().record_cleanup(
                    &handler.workspace_session_id,
                    None,
                    cgroup_path.as_ref().map(|path| path.exists()),
                    None,
                );
                sessions.remove(&handler.workspace_session_id);
                Ok(result)
            }
            Err(error) => Err(WorkspaceSessionError::Workspace(error)),
        }
    }
}

fn workspace_status<T>(result: &Result<T, WorkspaceSessionError>) -> RuntimeMetricStatus {
    match result {
        Ok(_) => RuntimeMetricStatus::Ok,
        Err(_) => RuntimeMetricStatus::Error,
    }
}

fn record_destroy_session_result(
    span: &Span,
    result: &Result<DestroyWorkspaceResult, WorkspaceSessionError>,
) {
    match result {
        Ok(result) => {
            span.record("status", "ok");
            span.record("lifetime_s", result.lifetime_s);
            span.record("evicted_upperdir_bytes", result.evicted_upperdir_bytes);
            if let Some(lease_released) = result.lease_released {
                span.record("lease_released", lease_released);
            }
            span.record("active_leases_after", result.active_leases_after as u64);
        }
        Err(error) => {
            span.record("status", "error");
            span.record("error_kind", error.kind());
        }
    }
}
