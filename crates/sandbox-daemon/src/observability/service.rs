//! Daemon observability query metadata and event access.

use std::path::Path;

use sandbox_config::configs::observability::ViewsConfig;
use sandbox_observability_telemetry::{
    record, ObservabilityPaths, Observer, ObserverConfig, Reader, Sink, WalkBudget,
};

use crate::rpc::ServerConfig;

pub struct DaemonObservability {
    sandbox_id: String,
    paths: ObservabilityPaths,
    observer: Observer,
    pub(crate) sampling: WalkBudget,
    pub(crate) views: ViewsConfig,
}

impl DaemonObservability {
    pub(crate) fn from_config(config: &ServerConfig) -> Option<Self> {
        let sandbox_id = config
            .sandbox_id
            .as_ref()
            .filter(|sandbox_id| !sandbox_id.is_empty())?
            .clone();
        let paths = ObservabilityPaths::from_socket_path(config.socket_path.clone()).ok()?;
        let observer = Observer::new(
            ObserverConfig {
                proc: record::proc::DAEMON,
                enabled: config.observability.enabled,
            },
            Sink::new(
                paths.log_path().to_path_buf(),
                config.observability.max_line_bytes,
            ),
        );
        Some(Self {
            sandbox_id,
            paths,
            observer,
            sampling: WalkBudget {
                max_nodes: config.observability.sampling.max_walk_nodes,
                max_depth: config.observability.sampling.max_walk_depth,
            },
            views: config.observability.views,
        })
    }

    /// A clone of the one process `Observer`. The runtime gets this same handle
    /// so daemon (`d-*`) and runtime spans share one id sequence and parent chain.
    pub(crate) fn observer(&self) -> Observer {
        self.observer.clone()
    }

    pub(super) fn reader(&self) -> Reader {
        Reader::new(
            self.paths.log_path().to_path_buf(),
            self.paths.rotated_log_path().to_path_buf(),
        )
    }

    pub(super) fn sandbox_id(&self) -> &str {
        &self.sandbox_id
    }

    pub(super) fn runtime_dir(&self) -> &Path {
        self.paths.daemon_runtime_dir()
    }
}
