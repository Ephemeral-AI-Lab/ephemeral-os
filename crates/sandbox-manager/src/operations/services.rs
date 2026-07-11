use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};

use crate::export_apply::ExportApplyCaps;
use crate::{
    SandboxDaemonClient, SandboxDaemonInstaller, SandboxId, SandboxResourceMetrics, SandboxRuntime,
    SandboxStore, WorkspaceRootPolicy,
};

pub(crate) const MAX_RESOURCE_HISTORY_MS: i64 = 600_000;

#[derive(Debug, Clone, Copy)]
pub(crate) struct ResourceSample {
    pub(crate) sampled_at_unix_ms: i64,
    pub(crate) metrics: SandboxResourceMetrics,
}

#[derive(Default)]
pub(crate) struct ResourceHistory {
    samples: Mutex<HashMap<SandboxId, VecDeque<ResourceSample>>>,
}

impl ResourceHistory {
    pub(crate) fn record(
        &self,
        id: SandboxId,
        sample: ResourceSample,
        window_ms: i64,
    ) -> Vec<ResourceSample> {
        let mut all = match self.samples.lock() {
            Ok(samples) => samples,
            Err(poisoned) => poisoned.into_inner(),
        };
        let samples = all.entry(id).or_default();
        samples.push_back(sample);
        let retain_after = sample
            .sampled_at_unix_ms
            .saturating_sub(MAX_RESOURCE_HISTORY_MS);
        while samples
            .front()
            .is_some_and(|entry| entry.sampled_at_unix_ms < retain_after)
        {
            samples.pop_front();
        }
        let window_start = sample.sampled_at_unix_ms.saturating_sub(window_ms);
        samples
            .iter()
            .copied()
            .filter(|entry| entry.sampled_at_unix_ms >= window_start)
            .collect()
    }
}

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
    pub workspace_roots: WorkspaceRootPolicy,
    pub(crate) resource_history: ResourceHistory,
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
            workspace_roots: WorkspaceRootPolicy::default(),
            resource_history: ResourceHistory::default(),
        }
    }
}
