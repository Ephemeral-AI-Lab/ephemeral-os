use std::path::{Path, PathBuf};

use crate::error::ConfigError;

/// A validated sandbox configuration path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigPath {
    path: PathBuf,
}

impl ConfigPath {
    /// Resolve `sandbox/config/prd.yml`.
    ///
    /// # Errors
    /// Returns an error if the sandbox workspace root cannot be derived from the
    /// crate layout.
    pub fn prd() -> Result<Self, ConfigError> {
        Ok(Self {
            path: workspace_root()?.join("config").join("prd.yml"),
        })
    }

    /// Resolve and validate a test-code supplied `*.test.yml` override.
    ///
    /// # Errors
    /// Returns an error if the path is not a sandbox-local test override path.
    pub fn test_override(path: &Path) -> Result<Self, ConfigError> {
        if !is_test_yml(path) {
            return Err(invalid_override(
                path,
                "override file name must end with .test.yml",
            ));
        }

        let root = workspace_root()?;
        let candidate = if path.is_absolute() {
            path.to_path_buf()
        } else {
            root.join(path)
        };
        let root = canonicalize_for_policy(&root, "sandbox workspace root")?;
        let prd = canonicalize_for_policy(
            &root.join("config").join("prd.yml"),
            "sandbox/config/prd.yml",
        )?;
        let canonical = candidate
            .canonicalize()
            .map_err(|source| ConfigError::Read {
                path: candidate.clone(),
                source,
            })?;

        if !canonical.starts_with(&root) {
            return Err(invalid_override(
                &canonical,
                "override path must be inside sandbox workspace",
            ));
        }
        if canonical == prd {
            return Err(invalid_override(
                &canonical,
                "override path must not resolve to sandbox/config/prd.yml",
            ));
        }

        Ok(Self { path: canonical })
    }

    /// Return the validated path.
    #[must_use]
    pub fn as_path(&self) -> &Path {
        &self.path
    }
}

fn workspace_root() -> Result<PathBuf, ConfigError> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .ancestors()
        .nth(2)
        .map(Path::to_path_buf)
        .ok_or(ConfigError::WorkspaceRoot { manifest_dir })
}

fn is_test_yml(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.ends_with(".test.yml"))
}

fn canonicalize_for_policy(path: &Path, label: &str) -> Result<PathBuf, ConfigError> {
    path.canonicalize()
        .map_err(|source| ConfigError::InvalidOverridePath {
            path: path.to_path_buf(),
            reason: format!("failed to canonicalize {label}: {source}"),
        })
}

fn invalid_override(path: &Path, reason: impl Into<String>) -> ConfigError {
    ConfigError::InvalidOverridePath {
        path: path.to_path_buf(),
        reason: reason.into(),
    }
}
