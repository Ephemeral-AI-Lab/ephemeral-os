use std::sync::Arc;

use crate::export_apply::ExportApplyCaps;
use crate::{SandboxDaemonClient, SandboxDaemonInstaller, SandboxRuntime, SandboxStore};

/// `manager.observability_snapshot` fan-out limits; the gateway overwrites
/// the default with the configured values before serving.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ObservabilitySnapshotLimits {
    pub max_concurrent_requests: usize,
    pub timeout_ms: u64,
}

impl Default for ObservabilitySnapshotLimits {
    fn default() -> Self {
        Self {
            max_concurrent_requests: 8,
            timeout_ms: 1_500,
        }
    }
}

pub struct ManagerServices {
    pub store: Arc<SandboxStore>,
    pub runtime: Arc<dyn SandboxRuntime>,
    pub daemon_installer: Arc<dyn SandboxDaemonInstaller>,
    pub daemon_client: Arc<dyn SandboxDaemonClient>,
    /// `manager.export` apply caps; the gateway overwrites the default with
    /// the configured values before serving.
    pub export_caps: ExportApplyCaps,
    pub snapshot_limits: ObservabilitySnapshotLimits,
}

impl ManagerServices {
    #[must_use]
    pub fn new(
        store: Arc<SandboxStore>,
        runtime: Arc<dyn SandboxRuntime>,
        daemon_installer: Arc<dyn SandboxDaemonInstaller>,
        daemon_client: Arc<dyn SandboxDaemonClient>,
    ) -> Self {
        Self {
            store,
            runtime,
            daemon_installer,
            daemon_client,
            export_caps: ExportApplyCaps::default(),
            snapshot_limits: ObservabilitySnapshotLimits::default(),
        }
    }
}
