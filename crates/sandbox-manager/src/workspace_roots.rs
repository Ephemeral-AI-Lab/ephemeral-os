use std::collections::BTreeSet;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use crate::ManagerError;

const MAX_WORKSPACE_DIRECTORIES: usize = 500;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceDirectory {
    pub name: String,
    pub path: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceDirectoryListing {
    pub path: Option<PathBuf>,
    pub parent: Option<PathBuf>,
    pub directories: Vec<WorkspaceDirectory>,
    pub truncated: bool,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct WorkspaceRootPolicy {
    roots: Vec<PathBuf>,
    enforce_roots: bool,
}

impl WorkspaceRootPolicy {
    /// Build an enforcing policy from configured host directories, resolving
    /// symlinks before the policy is used for browsing or sandbox creation.
    ///
    /// # Errors
    /// Returns an error when a configured root is missing or not a directory.
    pub fn configured(roots: Vec<PathBuf>) -> Result<Self, ManagerError> {
        let mut roots = roots
            .into_iter()
            .map(|root| canonical_directory(&root))
            .collect::<Result<Vec<_>, _>>()?;
        roots.sort();
        roots.dedup();
        Ok(Self {
            roots,
            enforce_roots: true,
        })
    }

    /// Build the default directory-browsing policy. It exposes only the
    /// gateway user's home directory while preserving legacy unrestricted
    /// create calls until an explicit `workspace_roots` allowlist is
    /// configured.
    ///
    /// # Errors
    /// Returns an error when the default browse root cannot be resolved.
    pub fn default_picker() -> Result<Self, ManagerError> {
        let root = env::var_os("HOME")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .or_else(|| env::current_dir().ok())
            .unwrap_or_else(|| PathBuf::from("/"));
        Ok(Self {
            roots: vec![canonical_directory(&root)?],
            enforce_roots: false,
        })
    }

    /// Return the canonical selected path when an allowlist is configured.
    /// An unenforced policy preserves the manager's legacy create behavior.
    ///
    /// # Errors
    /// Returns an error when a configured policy rejects the path.
    pub fn resolve(&self, candidate: PathBuf) -> Result<PathBuf, ManagerError> {
        if !self.enforce_roots {
            return Ok(candidate);
        }
        self.allowed_directory(&candidate)
    }

    /// List configured roots when no directory is selected, otherwise list up
    /// to 500 immediate subdirectories of the selected directory.
    ///
    /// # Errors
    /// Returns an error when the picker is not configured or the path cannot
    /// be read inside the configured roots.
    pub fn list(
        &self,
        selected: Option<PathBuf>,
    ) -> Result<WorkspaceDirectoryListing, ManagerError> {
        if self.roots.is_empty() {
            return Err(ManagerError::RuntimeFailed {
                message: "workspace directory selection is not configured".to_owned(),
            });
        }
        let Some(selected) = selected else {
            return Ok(WorkspaceDirectoryListing {
                path: None,
                parent: None,
                directories: self
                    .roots
                    .iter()
                    .take(MAX_WORKSPACE_DIRECTORIES)
                    .cloned()
                    .map(|path| WorkspaceDirectory {
                        name: path.to_string_lossy().into_owned(),
                        path,
                    })
                    .collect(),
                truncated: self.roots.len() > MAX_WORKSPACE_DIRECTORIES,
            });
        };
        let selected = self.allowed_directory(&selected)?;
        let parent = selected
            .parent()
            .map(Path::to_path_buf)
            .filter(|parent| self.allows(parent));
        let entries = fs::read_dir(&selected).map_err(|_| invalid_root(&selected))?;
        let mut seen = BTreeSet::new();
        let mut directories = Vec::with_capacity(MAX_WORKSPACE_DIRECTORIES);
        let mut truncated = false;
        for entry in entries.filter_map(Result::ok) {
            let path = match entry.path().canonicalize() {
                Ok(path) => path,
                Err(_) => continue,
            };
            if !path.is_dir() || !self.allows(&path) || !seen.insert(path.clone()) {
                continue;
            }
            if directories.len() == MAX_WORKSPACE_DIRECTORIES {
                truncated = true;
                break;
            }
            directories.push(WorkspaceDirectory {
                name: entry.file_name().to_string_lossy().into_owned(),
                path,
            });
        }
        directories.sort_by(|left, right| {
            left.name
                .to_lowercase()
                .cmp(&right.name.to_lowercase())
                .then_with(|| left.path.cmp(&right.path))
        });
        Ok(WorkspaceDirectoryListing {
            path: Some(selected),
            parent,
            directories,
            truncated,
        })
    }

    fn allowed_directory(&self, candidate: &Path) -> Result<PathBuf, ManagerError> {
        if !candidate.is_absolute() {
            return Err(invalid_root(candidate));
        }
        let canonical = canonical_directory(candidate)?;
        if self.allows(&canonical) {
            Ok(canonical)
        } else {
            Err(invalid_root(candidate))
        }
    }

    fn allows(&self, candidate: &Path) -> bool {
        self.roots.iter().any(|root| candidate.starts_with(root))
    }
}

fn canonical_directory(path: &Path) -> Result<PathBuf, ManagerError> {
    let canonical = path.canonicalize().map_err(|_| invalid_root(path))?;
    if canonical.is_dir() {
        Ok(canonical)
    } else {
        Err(invalid_root(path))
    }
}

fn invalid_root(path: &Path) -> ManagerError {
    ManagerError::InvalidWorkspaceRoot {
        value: path.to_string_lossy().into_owned(),
    }
}
