use std::path::{Path, PathBuf};

use thiserror::Error;

#[derive(Debug, Error)]
pub enum ObservabilityPathError {
    #[error("daemon socket path has no daemon runtime directory: {socket_path}")]
    MissingDaemonRuntimeDir { socket_path: PathBuf },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObservabilityPaths {
    daemon_runtime_dir: PathBuf,
    observability_dir: PathBuf,
    database_path: PathBuf,
    samples_log_path: PathBuf,
}

impl ObservabilityPaths {
    pub fn from_socket_path(socket_path: impl AsRef<Path>) -> Result<Self, ObservabilityPathError> {
        let socket_path = socket_path.as_ref();
        let daemon_runtime_dir = socket_path
            .parent()
            .filter(|path| !path.as_os_str().is_empty())
            .ok_or_else(|| ObservabilityPathError::MissingDaemonRuntimeDir {
                socket_path: socket_path.to_path_buf(),
            })?
            .to_path_buf();
        let observability_dir = daemon_runtime_dir.join("observability");
        let database_path = observability_dir.join("observability.sqlite");
        let samples_log_path = observability_dir.join("samples.ndjson");

        Ok(Self {
            daemon_runtime_dir,
            observability_dir,
            database_path,
            samples_log_path,
        })
    }

    pub fn daemon_runtime_dir(&self) -> &Path {
        &self.daemon_runtime_dir
    }

    pub fn observability_dir(&self) -> &Path {
        &self.observability_dir
    }

    pub fn database_path(&self) -> &Path {
        &self.database_path
    }

    pub fn samples_log_path(&self) -> &Path {
        &self.samples_log_path
    }
}
