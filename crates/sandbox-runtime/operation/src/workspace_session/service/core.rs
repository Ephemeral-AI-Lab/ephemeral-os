use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, MutexGuard};

use sandbox_observability::Observer;

use crate::workspace_crate::{WorkspaceRuntimeService, WorkspaceSessionId};
use crate::workspace_session::WorkspaceSessionError;

use super::model::WorkspaceSession;

pub struct WorkspaceSessionService {
    sessions: Mutex<HashMap<WorkspaceSessionId, WorkspaceSession>>,
    gates: Mutex<HashMap<WorkspaceSessionId, Arc<Mutex<()>>>>,
    workspace: Arc<WorkspaceRuntimeService>,
    cgroup_root: Option<PathBuf>,
    obs: Observer,
}

impl WorkspaceSessionService {
    #[must_use]
    pub fn new(workspace: Arc<WorkspaceRuntimeService>, obs: Observer) -> Self {
        Self::with_cgroup_root(workspace, None, obs)
    }

    #[must_use]
    pub fn with_cgroup_root(
        workspace: Arc<WorkspaceRuntimeService>,
        cgroup_root: Option<PathBuf>,
        obs: Observer,
    ) -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
            gates: Mutex::new(HashMap::new()),
            workspace,
            cgroup_root,
            obs,
        }
    }

    /// The per-session admission gate: the single serializer for exec
    /// launch, one-shot finalize, session file ops, capture, destroy, and
    /// remount. The gates map is locked only to clone the Arc — never wait
    /// on a gate while holding a map (lock order: gate → sessions map →
    /// storage writer lock).
    pub(crate) fn session_gate(&self, workspace_id: &WorkspaceSessionId) -> Arc<Mutex<()>> {
        self.gates
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .entry(workspace_id.clone())
            .or_default()
            .clone()
    }

    pub(crate) fn drop_session_gate(&self, workspace_id: &WorkspaceSessionId) {
        self.gates
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(workspace_id);
    }

    /// Snapshot of the live session ids for the post-commit remount sweep.
    #[must_use]
    pub fn session_ids(&self) -> Vec<WorkspaceSessionId> {
        self.sessions
            .lock()
            .map(|sessions| {
                let mut ids: Vec<WorkspaceSessionId> = sessions.keys().cloned().collect();
                ids.sort_by(|left, right| left.0.cmp(&right.0));
                ids
            })
            .unwrap_or_default()
    }

    #[must_use]
    pub(crate) fn workspace(&self) -> &Arc<WorkspaceRuntimeService> {
        &self.workspace
    }

    /// Resolve a workspace session to its isolated-network IP.
    ///
    /// `Err(NotFound)` means no such session; `Ok(None)` means the session
    /// exists but has no reachable isolated IP (shared network or no veth);
    /// `Ok(Some(ip))` is the workspace IP a forwarder can dial.
    ///
    /// # Errors
    /// Returns [`WorkspaceSessionError::NotFound`] for an unknown session, or a
    /// lock/runtime error when session or workspace state cannot be read.
    pub fn isolated_ip(
        &self,
        workspace_id: &WorkspaceSessionId,
    ) -> Result<Option<std::net::Ipv4Addr>, WorkspaceSessionError> {
        if !self.lock_sessions()?.contains_key(workspace_id) {
            return Err(WorkspaceSessionError::not_found(workspace_id));
        }
        Ok(self.workspace.isolated_ip(workspace_id)?)
    }

    #[must_use]
    pub(crate) fn obs(&self) -> &Observer {
        &self.obs
    }

    /// Create the leaf workspace cgroup `R/workspace-<wsid>` when a delegated
    /// cgroup root is configured. Best-effort: directory creation never blocks
    /// session creation, and an unconfigured root yields `None`.
    pub(crate) fn prepare_workspace_cgroup(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> Option<PathBuf> {
        let path = self
            .cgroup_root
            .as_ref()?
            .join(format!("workspace-{}", workspace_session_id.0));
        let _ = std::fs::create_dir_all(&path);
        Some(path)
    }

    pub(crate) fn lock_sessions(
        &self,
    ) -> Result<MutexGuard<'_, HashMap<WorkspaceSessionId, WorkspaceSession>>, WorkspaceSessionError>
    {
        self.sessions
            .lock()
            .map_err(|_| WorkspaceSessionError::LockPoisoned)
    }
}
