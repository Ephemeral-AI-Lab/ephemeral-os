use std::path::{Path, PathBuf};

use thiserror::Error;

#[derive(Debug, Error)]
pub enum ObservabilityPathError {
    #[error("daemon socket path has no runtime directory: {socket_path}")]
    MissingRuntimeDir { socket_path: PathBuf },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObservabilityPaths {
    runtime_dir: PathBuf,
    observability_dir: PathBuf,
    database_path: PathBuf,
}

impl ObservabilityPaths {
    pub fn from_socket_path(socket_path: impl AsRef<Path>) -> Result<Self, ObservabilityPathError> {
        let socket_path = socket_path.as_ref();
        let runtime_dir = socket_path
            .parent()
            .filter(|path| !path.as_os_str().is_empty())
            .ok_or_else(|| ObservabilityPathError::MissingRuntimeDir {
                socket_path: socket_path.to_path_buf(),
            })?
            .to_path_buf();
        let observability_dir = runtime_dir.join("observability");
        let database_path = observability_dir.join("observability.sqlite");

        Ok(Self {
            runtime_dir,
            observability_dir,
            database_path,
        })
    }

    pub fn runtime_dir(&self) -> &Path {
        &self.runtime_dir
    }

    pub fn observability_dir(&self) -> &Path {
        &self.observability_dir
    }

    pub fn database_path(&self) -> &Path {
        &self.database_path
    }
}
