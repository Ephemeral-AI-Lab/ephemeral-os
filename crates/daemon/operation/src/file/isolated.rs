use std::collections::BTreeMap;
use std::io::Read;
use std::path::{Component, Path, PathBuf};

use layerstack::{LayerRef, Manifest, MergedView, WorkspaceBinding, MANIFEST_SCHEMA_VERSION};
use serde_json::json;
use trace::usize_to_f64_saturating;

use super::direct::{api_error, parse_layer_path, read_error, resolve_layer_path};
use super::{
    ChangedPathKind, FileBackend, FileOpsError, Mutation, MutationCore, MutationKind,
    MutationOutcome, MutationSource, MutationStatus, ReadBytes, ResolvedWorkspacePath,
    WorkspaceKind, WorkspaceTimings,
};

#[derive(Debug, Clone)]
pub struct IsolatedBackend {
    pub layer_stack_root: PathBuf,
    pub workspace_root: PathBuf,
    pub upperdir: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
}

impl FileBackend for IsolatedBackend {
    fn workspace_kind(&self) -> WorkspaceKind {
        WorkspaceKind::Isolated
    }

    fn mutation_source(&self, _kind: MutationKind) -> MutationSource {
        MutationSource::IsolatedWorkspace
    }

    fn resolve_path(&self, request_path: &str) -> Result<ResolvedWorkspacePath, FileOpsError> {
        let binding = WorkspaceBinding {
            workspace_root: self.workspace_root.to_string_lossy().into_owned(),
            layer_stack_root: self.layer_stack_root.to_string_lossy().into_owned(),
            active_manifest_version: self.manifest_version,
            active_root_hash: self.manifest_root_hash.clone(),
            base_manifest_version: self.manifest_version,
            base_root_hash: self.manifest_root_hash.clone(),
        };
        resolve_layer_path(&binding, request_path)
    }

    fn read_bytes(
        &self,
        path: &ResolvedWorkspacePath,
        max_bytes: usize,
    ) -> Result<ReadBytes, FileOpsError> {
        let read_start = std::time::Instant::now();
        let layer_path = parse_layer_path(&path.path)?;
        let (bytes, exists) = self.read_current(layer_path.as_str(), max_bytes)?;
        let mut timings = self.timings(0);
        timings.insert(
            "sandbox.file.read.layer_stack_read_s".to_owned(),
            json!(read_start.elapsed().as_secs_f64()),
        );
        Ok(ReadBytes {
            bytes,
            exists,
            manifest_version: Some(self.manifest_version),
            timings,
        })
    }

    fn apply(&self, mutation: Mutation) -> Result<MutationOutcome, FileOpsError> {
        let layer_path = parse_layer_path(&mutation.path.path)?;
        let target = prepare_upperdir_target(&self.upperdir, layer_path.as_str())?;
        std::fs::write(target, &mutation.content).map_err(api_error)?;
        let changed_paths = vec![layer_path.as_str().to_owned()];
        Ok(MutationOutcome {
            core: MutationCore {
                success: true,
                conflict: None,
                conflict_reason: None,
                changed_path_kinds: BTreeMap::from([(
                    layer_path.as_str().to_owned(),
                    ChangedPathKind::Write,
                )]),
                changed_paths,
                mutation_source: Some(self.mutation_source(mutation.kind)),
                timings: self.timings(1),
            },
            workspace_kind: WorkspaceKind::Isolated,
            published: false,
            status: MutationStatus::Committed,
            ..MutationOutcome::default()
        })
    }
}

impl IsolatedBackend {
    fn read_current(
        &self,
        layer_path: &str,
        max_bytes: usize,
    ) -> Result<(Option<Vec<u8>>, bool), FileOpsError> {
        let upper_path = self.upperdir.join(layer_path);
        match std::fs::symlink_metadata(&upper_path) {
            Ok(metadata) if metadata.is_file() => {
                return Ok((
                    Some(read_upper_file_limited(&upper_path, &metadata, max_bytes)?),
                    true,
                ));
            }
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Ok((
                    Some(
                        std::fs::read_link(upper_path)
                            .map_err(api_error)?
                            .to_string_lossy()
                            .as_bytes()
                            .to_vec(),
                    ),
                    true,
                ));
            }
            Ok(_) => return Ok((None, false)),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(api_error(error)),
        }
        MergedView::new(self.layer_stack_root.clone())
            .read_bytes_limited(layer_path, &self.snapshot_manifest(), max_bytes)
            .map_err(read_error)
    }

    fn snapshot_manifest(&self) -> Manifest {
        Manifest {
            version: self.manifest_version,
            schema_version: MANIFEST_SCHEMA_VERSION,
            layers: self
                .layer_paths
                .iter()
                .enumerate()
                .map(|(index, path)| LayerRef {
                    layer_id: format!("isolated-{index}"),
                    path: relative_layer_path(&self.layer_stack_root, path),
                })
                .collect(),
        }
    }

    fn timings(&self, changed_path_count: usize) -> WorkspaceTimings {
        BTreeMap::from([(
            "resource.command_exec.changed_path_count".to_owned(),
            json!(usize_to_f64_saturating(changed_path_count)),
        )])
    }
}

fn read_upper_file_limited(
    path: &Path,
    metadata: &std::fs::Metadata,
    max_bytes: usize,
) -> Result<Vec<u8>, FileOpsError> {
    let limit = u64::try_from(max_bytes).unwrap_or(u64::MAX);
    if metadata.len() > limit {
        return Err(FileOpsError::invalid_request(format!(
            "file too large: {} > {} bytes",
            metadata.len(),
            max_bytes
        )));
    }
    let file = std::fs::File::open(path).map_err(api_error)?;
    let mut bytes = Vec::new();
    file.take(limit.saturating_add(1))
        .read_to_end(&mut bytes)
        .map_err(api_error)?;
    if bytes.len() > max_bytes {
        return Err(FileOpsError::invalid_request(format!(
            "file too large: {} > {} bytes",
            bytes.len(),
            max_bytes
        )));
    }
    Ok(bytes)
}

fn relative_layer_path(layer_stack_root: &Path, path: &Path) -> String {
    path.strip_prefix(layer_stack_root)
        .unwrap_or(path)
        .to_string_lossy()
        .into_owned()
}

fn prepare_upperdir_target(upperdir: &Path, layer_path: &str) -> Result<PathBuf, FileOpsError> {
    let relative = Path::new(layer_path);
    if let Some(parent) = relative.parent() {
        let mut current = upperdir.to_path_buf();
        for component in parent.components() {
            match component {
                Component::Normal(segment) => {
                    current.push(segment);
                    ensure_upperdir_parent(&current)?;
                }
                Component::CurDir => {}
                _ => {
                    return Err(FileOpsError::invalid_request(
                        "isolated file path contains unsupported parent component",
                    ));
                }
            }
        }
    }
    let target = upperdir.join(relative);
    reject_symlink_target(&target)?;
    Ok(target)
}

fn ensure_upperdir_parent(path: &Path) -> Result<(), FileOpsError> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(FileOpsError::invalid_request(
            format!("isolated file parent is a symlink: {}", path.display()),
        )),
        Ok(metadata) if metadata.is_dir() => Ok(()),
        Ok(_) => Err(FileOpsError::invalid_request(format!(
            "isolated file parent is not a directory: {}",
            path.display()
        ))),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            match std::fs::create_dir(path) {
                Ok(()) => Ok(()),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                    ensure_upperdir_parent(path)
                }
                Err(error) => Err(api_error(error)),
            }
        }
        Err(error) => Err(api_error(error)),
    }
}

fn reject_symlink_target(path: &Path) -> Result<(), FileOpsError> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(FileOpsError::invalid_request(
            format!("isolated file target is a symlink: {}", path.display()),
        )),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(api_error(error)),
    }
}

#[cfg(test)]
mod tests {
    use std::path::{Path, PathBuf};

    use crate::file::{FileBackend, Mutation, MutationKind, ReadBytes, ResolvedWorkspacePath};

    use super::IsolatedBackend;

    type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

    #[test]
    #[cfg(unix)]
    fn apply_rejects_symlink_parent_inside_upperdir() -> TestResult {
        let root = test_root("symlink-parent")?;
        let outside = root.join("outside");
        let upperdir = root.join("upper");
        std::fs::create_dir_all(&outside)?;
        std::fs::create_dir_all(&upperdir)?;
        std::os::unix::fs::symlink(&outside, upperdir.join("link"))?;

        let Err(error) = backend(&root).apply(mutation("link/escaped.txt", b"escaped")) else {
            return Err("symlink parent write unexpectedly succeeded".into());
        };

        assert_eq!(error.kind, "invalid_request");
        assert!(error.message.contains("parent is a symlink"));
        assert!(!outside.join("escaped.txt").exists());
        let _ = std::fs::remove_dir_all(root);
        Ok(())
    }

    #[test]
    #[cfg(unix)]
    fn apply_rejects_symlink_target_inside_upperdir() -> TestResult {
        let root = test_root("symlink-target")?;
        let outside = root.join("outside.txt");
        let upperdir = root.join("upper");
        std::fs::create_dir_all(&upperdir)?;
        std::fs::write(&outside, b"outside")?;
        std::os::unix::fs::symlink(&outside, upperdir.join("link.txt"))?;

        let Err(error) = backend(&root).apply(mutation("link.txt", b"mutated")) else {
            return Err("symlink target write unexpectedly succeeded".into());
        };

        assert_eq!(error.kind, "invalid_request");
        assert!(error.message.contains("target is a symlink"));
        assert_eq!(std::fs::read(&outside)?, b"outside");
        let _ = std::fs::remove_dir_all(root);
        Ok(())
    }

    fn backend(root: &Path) -> IsolatedBackend {
        IsolatedBackend {
            layer_stack_root: root.join("layers"),
            workspace_root: root.join("workspace"),
            upperdir: root.join("upper"),
            layer_paths: Vec::new(),
            manifest_version: 1,
            manifest_root_hash: "root".to_owned(),
        }
    }

    fn mutation(path: &str, content: &[u8]) -> Mutation {
        Mutation {
            kind: MutationKind::Write,
            path: ResolvedWorkspacePath::new(path),
            content: content.to_vec(),
            base: ReadBytes {
                bytes: None,
                exists: false,
                manifest_version: Some(1),
                timings: Default::default(),
            },
        }
    }

    fn test_root(name: &str) -> std::io::Result<PathBuf> {
        let root = std::env::temp_dir().join(format!(
            "operation-file-isolated-{name}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root)?;
        Ok(root)
    }
}
