use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};

use layerstack::{LayerRef, Manifest, MergedView, WorkspaceBinding, MANIFEST_SCHEMA_VERSION};
use rustix::fd::{AsFd, OwnedFd};
use rustix::fs::{
    mkdirat, openat, renameat, statat, unlinkat, AtFlags, FileType, Mode, OFlags, CWD,
};
use rustix::io::Errno;
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
        write_upperdir_file(&self.upperdir, layer_path.as_str(), &mutation.content)?;
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

fn write_upperdir_file(
    upperdir: &Path,
    layer_path: &str,
    content: &[u8],
) -> Result<(), FileOpsError> {
    write_upperdir_file_with_hook(upperdir, layer_path, content, || Ok(()))
}

fn write_upperdir_file_with_hook(
    upperdir: &Path,
    layer_path: &str,
    content: &[u8],
    before_rename: impl FnOnce() -> Result<(), FileOpsError>,
) -> Result<(), FileOpsError> {
    let components = layer_path_components(layer_path)?;
    let Some((target_name, parent_components)) = components.split_last() else {
        return Err(FileOpsError::invalid_request(
            "isolated file path must include a target name",
        ));
    };
    let parent = open_upperdir_parent(upperdir, parent_components)?;
    reject_symlink_target_at(parent.as_fd(), target_name)?;
    let temp_name = create_temp_file_at(parent.as_fd(), content)?;
    let commit = before_rename().and_then(|()| {
        renameat(
            parent.as_fd(),
            temp_name.as_str(),
            parent.as_fd(),
            target_name.as_str(),
        )
        .map_err(|err| api_error(format!("rename isolated file target: {err}")))
    });
    if commit.is_err() {
        let _ = unlinkat(parent.as_fd(), temp_name.as_str(), AtFlags::empty());
    }
    commit
}

fn layer_path_components(layer_path: &str) -> Result<Vec<String>, FileOpsError> {
    Path::new(layer_path)
        .components()
        .filter_map(|component| match component {
            Component::Normal(segment) => {
                Some(segment.to_str().map(str::to_owned).ok_or_else(|| {
                    FileOpsError::invalid_request("isolated file path contains non-utf8 component")
                }))
            }
            Component::CurDir => None,
            _ => Some(Err(FileOpsError::invalid_request(
                "isolated file path contains unsupported parent component",
            ))),
        })
        .collect()
}

fn open_upperdir_parent(
    upperdir: &Path,
    parent_components: &[String],
) -> Result<DirFd, FileOpsError> {
    let root_fd = open_dir_path(upperdir)?;
    parent_components
        .iter()
        .try_fold(root_fd, |parent, component| {
            open_or_create_child_dir(&parent, component)
        })
}

fn open_dir_path(path: &Path) -> Result<DirFd, FileOpsError> {
    openat(
        CWD,
        path,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::CLOEXEC | OFlags::NOFOLLOW,
        Mode::empty(),
    )
    .map(DirFd)
    .map_err(|err| map_parent_dir_error(path.display().to_string(), err))
}

fn open_or_create_child_dir(parent: &DirFd, component: &str) -> Result<DirFd, FileOpsError> {
    match open_child_dir(parent.as_fd(), component) {
        Ok(fd) => return Ok(fd),
        Err(Errno::NOENT) => {}
        Err(err) => {
            return Err(map_child_parent_dir_error(parent.as_fd(), component, err));
        }
    }
    match mkdirat(parent.as_fd(), component, Mode::from_raw_mode(0o755)) {
        Ok(()) | Err(Errno::EXIST) => {}
        Err(err) => return Err(map_parent_dir_error(component.to_owned(), err)),
    }
    open_child_dir(parent.as_fd(), component)
        .map_err(|err| map_child_parent_dir_error(parent.as_fd(), component, err))
}

fn open_child_dir(parent_fd: impl AsFd, component: &str) -> Result<DirFd, Errno> {
    openat(
        parent_fd,
        component,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::CLOEXEC | OFlags::NOFOLLOW,
        Mode::empty(),
    )
    .map(DirFd)
}

fn map_parent_dir_error(path: String, error: Errno) -> FileOpsError {
    match error {
        Errno::LOOP => {
            FileOpsError::invalid_request(format!("isolated file parent is a symlink: {path}"))
        }
        Errno::NOTDIR => FileOpsError::invalid_request(format!(
            "isolated file parent is not a directory: {path}"
        )),
        other => api_error(format!("open isolated file parent {path}: {other}")),
    }
}

fn map_child_parent_dir_error(parent_fd: impl AsFd, component: &str, error: Errno) -> FileOpsError {
    if matches!(error, Errno::LOOP | Errno::NOTDIR) && child_is_symlink(parent_fd, component) {
        return FileOpsError::invalid_request(format!(
            "isolated file parent is a symlink: {component}"
        ));
    }
    map_parent_dir_error(component.to_owned(), error)
}

fn child_is_symlink(parent_fd: impl AsFd, component: &str) -> bool {
    statat(parent_fd, component, AtFlags::SYMLINK_NOFOLLOW)
        .ok()
        .is_some_and(|stat| FileType::from_raw_mode(stat.st_mode) == FileType::Symlink)
}

fn reject_symlink_target_at(parent_fd: impl AsFd, target_name: &str) -> Result<(), FileOpsError> {
    match statat(parent_fd, target_name, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(stat) if FileType::from_raw_mode(stat.st_mode) == FileType::Symlink => {
            Err(FileOpsError::invalid_request(format!(
                "isolated file target is a symlink: {target_name}"
            )))
        }
        Ok(_) | Err(Errno::NOENT) => Ok(()),
        Err(error) => Err(api_error(format!(
            "stat isolated file target {target_name}: {error}"
        ))),
    }
}

fn create_temp_file_at(parent_fd: impl AsFd, content: &[u8]) -> Result<String, FileOpsError> {
    for _ in 0..16 {
        let temp_name = format!(".eos-write-{}.tmp", uuid::Uuid::new_v4());
        match openat(
            parent_fd.as_fd(),
            temp_name.as_str(),
            OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::CLOEXEC | OFlags::NOFOLLOW,
            Mode::from_raw_mode(0o644),
        ) {
            Ok(fd) => {
                let mut file = std::fs::File::from(fd);
                file.write_all(content).map_err(api_error)?;
                file.sync_all().map_err(api_error)?;
                return Ok(temp_name);
            }
            Err(Errno::EXIST) => continue,
            Err(error) => {
                return Err(api_error(format!(
                    "create isolated file temp {temp_name}: {error}"
                )));
            }
        }
    }
    Err(api_error(
        "create isolated file temp: exhausted unique temp names",
    ))
}

struct DirFd(OwnedFd);

impl DirFd {
    fn as_fd(&self) -> impl AsFd + '_ {
        self.0.as_fd()
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

    #[test]
    #[cfg(unix)]
    fn apply_replaces_raced_symlink_target_without_mutating_outside() -> TestResult {
        let root = test_root("symlink-target-race")?;
        let upperdir = root.join("upper");
        let outside = root.join("outside.txt");
        std::fs::create_dir_all(&upperdir)?;
        std::fs::write(&outside, b"outside")?;

        super::write_upperdir_file_with_hook(&upperdir, "race.txt", b"inside", || {
            std::os::unix::fs::symlink(&outside, upperdir.join("race.txt"))
                .map_err(super::api_error)
        })?;

        assert_eq!(std::fs::read(&outside)?, b"outside");
        assert_eq!(std::fs::read(upperdir.join("race.txt"))?, b"inside");
        let _ = std::fs::remove_dir_all(root);
        Ok(())
    }

    #[test]
    fn apply_rejects_non_directory_parent_inside_upperdir() -> TestResult {
        let root = test_root("non-directory-parent")?;
        let upperdir = root.join("upper");
        std::fs::create_dir_all(&upperdir)?;
        std::fs::write(upperdir.join("file"), b"not a dir")?;

        let Err(error) = backend(&root).apply(mutation("file/child.txt", b"child")) else {
            return Err("non-directory parent write unexpectedly succeeded".into());
        };

        assert_eq!(error.kind, "invalid_request");
        assert!(error.message.contains("parent is not a directory"));
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
