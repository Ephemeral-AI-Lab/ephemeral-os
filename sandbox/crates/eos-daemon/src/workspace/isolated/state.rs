//! Daemon-local isolated-workspace session state.
//!
//! The daemon is the composition root: it owns the layer stack (lease
//! acquire/release) and the [`IsolatedSessions`] registry, which never touches
//! storage itself — snapshots go in as plain fields and `lease_id`s come back
//! out at exit for release here.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, OnceLock, PoisonError};

use eos_config::configs::isolated_workspace::{
    IsolatedWorkspaceConfig, Rfc1918Egress as ConfigRfc1918Egress,
};
use eos_isolated_workspace::{
    IsolatedError, IsolatedSessions, IsolatedSnapshot, ResourceCaps,
    Rfc1918Egress as RuntimeRfc1918Egress,
};
use eos_layerstack::{read_workspace_binding, LayerStack};

use super::setup_error;

pub(super) struct DaemonIsolatedState {
    pub(super) layer_stack_root: PathBuf,
    pub(super) stack: LayerStack,
    pub(super) sessions: IsolatedSessions,
}

impl DaemonIsolatedState {
    /// Acquire a snapshot lease for `caller_id` and shape it for `enter`.
    pub(super) fn acquire_snapshot(
        &self,
        caller_id: &str,
    ) -> Result<IsolatedSnapshot, IsolatedError> {
        let lease = self
            .stack
            .acquire_snapshot(&format!("isolated-{caller_id}"))
            .map_err(setup_error)?;
        Ok(IsolatedSnapshot {
            lease_id: lease.lease_id,
            manifest_version: lease.manifest_version,
            manifest_root_hash: lease.root_hash,
            layer_paths: lease.layer_paths.into_iter().map(PathBuf::from).collect(),
        })
    }

    /// Best-effort lease release; returns whether the lease was held.
    pub(super) fn release_lease(&mut self, lease_id: &str) -> Option<bool> {
        self.stack.release_lease(lease_id).ok()
    }

    pub(super) fn active_lease_count(&self) -> usize {
        self.stack.active_lease_count()
    }
}

pub(crate) fn configure_isolated_workspace(config: &IsolatedWorkspaceConfig) {
    let mut guard = isolated_workspace_config_cell()
        .write()
        .unwrap_or_else(PoisonError::into_inner);
    *guard = config.clone();
}

pub(super) fn ensure_state(root: &Path) -> Result<(), IsolatedError> {
    let root = normalized_root(root);
    {
        let mut guard = lock_state_cell();
        if let Some(state) = guard.as_mut() {
            if state.layer_stack_root != root {
                // Block rebinding to a new root only while an isolated workspace
                // is open: those handles pin leases/namespaces on the old root.
                // (Isolated command sessions belong to an open caller, so this
                // already covers them; ephemeral command sessions are unrelated
                // to the isolated manager's binding and must not block a rebind.)
                let open_callers = state.sessions.list_open_callers();
                if !open_callers.is_empty() {
                    return Err(IsolatedError::SetupFailed {
                        step: format!(
                            "isolated workspace manager is bound to {} with active callers",
                            state.layer_stack_root.display()
                        ),
                    });
                }
                state.sessions.reap_orphan_resources();
                *guard = None;
            }
        }
        if guard.is_none() {
            let config = isolated_workspace_config();
            let mut caps = resource_caps_from_config(&config);
            if !caps.enabled {
                return Err(IsolatedError::FeatureDisabled);
            }
            if let Some(binding) = read_workspace_binding(&root).map_err(setup_error)? {
                caps.eos_workspace_root = binding.workspace_root;
            }
            let mut stack = LayerStack::open(root.clone()).map_err(setup_error)?;
            let mut sessions = IsolatedSessions::with_scratch_root(caps, config.scratch_root);
            let orphan_lease_ids = sessions.initialize()?;
            for lease_id in orphan_lease_ids {
                let _ = stack.release_lease(&lease_id);
            }
            *guard = Some(DaemonIsolatedState {
                layer_stack_root: root,
                stack,
                sessions,
            });
        }
    }
    Ok(())
}

fn normalized_root(root: &Path) -> PathBuf {
    root.canonicalize().unwrap_or_else(|_| root.to_path_buf())
}

pub(super) fn isolated_workspace_config() -> IsolatedWorkspaceConfig {
    isolated_workspace_config_cell()
        .read()
        .unwrap_or_else(PoisonError::into_inner)
        .clone()
}

fn isolated_workspace_config_cell() -> &'static std::sync::RwLock<IsolatedWorkspaceConfig> {
    static CONFIG: OnceLock<std::sync::RwLock<IsolatedWorkspaceConfig>> = OnceLock::new();
    CONFIG.get_or_init(|| std::sync::RwLock::new(default_isolated_workspace_config()))
}

pub(super) fn default_isolated_workspace_config() -> IsolatedWorkspaceConfig {
    IsolatedWorkspaceConfig {
        enabled: false,
        scratch_root: PathBuf::from("/eos/scratch/isolated"),
        ttl_s: 1800.0,
        total_cap: 5,
        upperdir_bytes: 1_073_741_824,
        memavail_fraction: 0.5,
        setup_timeout_s: 30.0,
        exit_grace_s: 0.25,
        rfc1918_egress: ConfigRfc1918Egress::Allow,
        fallback_dns: "1.1.1.1".to_owned(),
        workspace_root: PathBuf::from("/testbed"),
        sample_interval_s: 0.5,
    }
}

fn resource_caps_from_config(config: &IsolatedWorkspaceConfig) -> ResourceCaps {
    ResourceCaps {
        enabled: config.enabled,
        ttl_s: config.ttl_s,
        total_cap: config.total_cap,
        upperdir_bytes: config.upperdir_bytes,
        memavail_fraction: config.memavail_fraction,
        setup_timeout_s: config.setup_timeout_s,
        exit_grace_s: config.exit_grace_s,
        rfc1918_egress: match config.rfc1918_egress {
            ConfigRfc1918Egress::Allow => RuntimeRfc1918Egress::Allow,
            ConfigRfc1918Egress::Deny => RuntimeRfc1918Egress::Deny,
        },
        fallback_dns: config.fallback_dns.clone(),
        eos_workspace_root: config.workspace_root.to_string_lossy().into_owned(),
        sample_interval_s: config.sample_interval_s,
    }
}

pub(super) fn with_state<T>(
    f: impl FnOnce(&mut DaemonIsolatedState) -> Result<T, IsolatedError>,
) -> Result<T, IsolatedError> {
    lock_state_cell()
        .as_mut()
        .ok_or(IsolatedError::FeatureDisabled)
        .and_then(f)
}

fn state_cell() -> &'static Mutex<Option<DaemonIsolatedState>> {
    static STATE: OnceLock<Mutex<Option<DaemonIsolatedState>>> = OnceLock::new();
    STATE.get_or_init(|| Mutex::new(None))
}

pub(super) fn lock_state_cell() -> MutexGuard<'static, Option<DaemonIsolatedState>> {
    state_cell().lock().unwrap_or_else(PoisonError::into_inner)
}

pub(super) fn reset_test_manager_file() {
    let session_root = isolated_workspace_config().scratch_root;
    let _ = std::fs::remove_dir_all(&session_root);
    if std::fs::create_dir_all(&session_root).is_err() {
        return;
    }
    let _ = std::fs::write(
        session_root.join("manager.json"),
        br#"{"schema_version":1,"handles":[]}"#,
    );
}
