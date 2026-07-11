use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use sandbox_observability_telemetry::Observer;

use crate::layerstack::LayerStackService;
use crate::workspace_crate::{WorkspaceRuntimeService, WorkspaceSessionId};
use crate::workspace_session::WorkspaceSessionError;

use super::model::WorkspaceSession;

pub struct WorkspaceSessionService {
    sessions: Mutex<HashMap<WorkspaceSessionId, WorkspaceSession>>,
    gates: Mutex<HashMap<WorkspaceSessionId, Arc<Mutex<()>>>>,
    workspace: Arc<WorkspaceRuntimeService>,
    layerstack: Arc<LayerStackService>,
    cgroup_root: Option<PathBuf>,
    obs: Observer,
}

impl WorkspaceSessionService {
    #[must_use]
    pub fn new(
        workspace: Arc<WorkspaceRuntimeService>,
        layerstack: Arc<LayerStackService>,
        obs: Observer,
    ) -> Self {
        Self::with_cgroup_root(workspace, layerstack, None, obs)
    }

    #[must_use]
    pub fn with_cgroup_root(
        workspace: Arc<WorkspaceRuntimeService>,
        layerstack: Arc<LayerStackService>,
        cgroup_root: Option<PathBuf>,
        obs: Observer,
    ) -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
            gates: Mutex::new(HashMap::new()),
            workspace,
            layerstack,
            cgroup_root,
            obs,
        }
    }

    /// The per-session admission gate: the single serializer for command
    /// admission, completion, and finalization, session file ops, remounts,
    /// and guarded/faulty destroys. It does not serialize any public capture —
    /// capture exists only inside the finalize runner, which runs under the
    /// gate already held by the completing path. The gates map is locked only
    /// to clone or drop an Arc — never wait on a gate while holding a map
    /// (lock order: gate → sessions map → storage writer lock; the gates map
    /// may briefly take `sessions` inside [`Self::discard_resurrected_gate`],
    /// so nothing may take the gates map while holding `sessions`).
    pub(crate) fn session_gate(&self, workspace_id: &WorkspaceSessionId) -> Arc<Mutex<()>> {
        self.gates
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .entry(workspace_id.clone())
            .or_default()
            .clone()
    }

    pub(crate) fn drop_session_gate(&self, workspace_id: &WorkspaceSessionId) {
        self.gates
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .remove(workspace_id);
    }

    /// Gates-map hygiene (§2.3): a gate-then-resolve path that failed
    /// `not_found` removes the gates-map entry it may have resurrected —
    /// only when the map entry is still the same `Arc` and the sessions map
    /// has no entry for the id, so a concurrently re-created session or a
    /// stuck `finalize_failed` session keeps its gate.
    pub(crate) fn discard_resurrected_gate(
        &self,
        workspace_id: &WorkspaceSessionId,
        gate: &Arc<Mutex<()>>,
    ) {
        let mut gates = self.gates.lock().unwrap_or_else(PoisonError::into_inner);
        let same_entry = gates
            .get(workspace_id)
            .is_some_and(|entry| Arc::ptr_eq(entry, gate));
        if !same_entry {
            return;
        }
        let session_absent = self
            .sessions
            .lock()
            .map(|sessions| !sessions.contains_key(workspace_id))
            .unwrap_or(false);
        if session_absent {
            gates.remove(workspace_id);
        }
    }

    /// Number of live gates-map entries. Observability for the gates-map
    /// hygiene rule; not part of the operational API.
    #[doc(hidden)]
    #[must_use]
    pub fn gate_entry_count(&self) -> usize {
        self.gates
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .len()
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

    #[must_use]
    pub(crate) fn layerstack(&self) -> &Arc<LayerStackService> {
        &self.layerstack
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
