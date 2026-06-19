use std::io::{ErrorKind, Read};
use std::path::{Path, PathBuf};

use crate::error::LayerStackError;
use crate::fs::{join_layer_path, remove_path, resolve_layer_path, validate_layer_ref};
use crate::model::{LayerPath, LayerRef, Manifest};

use crate::whiteout::{
    is_kernel_whiteout, is_kernel_whiteout_meta, logical_whiteout_path_for_target,
    LOGICAL_WHITEOUT_PREFIX, OPAQUE_MARKER,
};

#[derive(Debug)]
pub struct MergedView {
    storage_root: PathBuf,
}

impl MergedView {
    #[must_use]
    pub const fn new(storage_root: PathBuf) -> Self {
        Self { storage_root }
    }

    pub fn read_bytes(
        &self,
        path: &str,
        manifest: &Manifest,
    ) -> Result<(Option<Vec<u8>>, bool), LayerStackError> {
        self.read_bytes_limited(path, manifest, usize::MAX)
    }

    pub fn read_bytes_limited(
        &self,
        path: &str,
        manifest: &Manifest,
        max_bytes: usize,
    ) -> Result<(Option<Vec<u8>>, bool), LayerStackError> {
        let rel = LayerPath::parse(path)?;
        for layer in &manifest.layers {
            let layer_dir = self.layer_dir(layer)?;
            if Self::is_whiteouted(&layer_dir, rel.as_str()) {
                return Ok((None, false));
            }
            if Self::lookup_blocked_by_layer(&layer_dir, rel.as_str()) {
                return Ok((None, false));
            }
            let target = join_layer_path(&layer_dir, rel.as_str());
            match std::fs::symlink_metadata(&target) {
                Ok(meta) if meta.file_type().is_symlink() => {
                    let target = std::fs::read_link(&target)
                        .map_err(|err| stale_layer_error(layer, rel.as_str(), Some(&err)))?;
                    return Ok((Some(target.to_string_lossy().as_bytes().to_vec()), true));
                }
                Ok(meta) if meta.is_file() => {
                    let bytes = match read_file_limited(&target, &meta, max_bytes) {
                        Ok(bytes) => bytes,
                        Err(err @ LayerStackError::FileTooLarge { .. }) => return Err(err),
                        Err(err) => return Err(stale_layer_error(layer, rel.as_str(), Some(&err))),
                    };
                    return Ok((Some(bytes), true));
                }
                Ok(_) => return Err(stale_layer_error(layer, rel.as_str(), None)),
                Err(err) if err.kind() == ErrorKind::NotFound => {}
                Err(err) => return Err(stale_layer_error(layer, rel.as_str(), Some(&err))),
            }
        }
        Ok((None, false))
    }

    pub fn project(&self, destination: &Path, manifest: &Manifest) -> Result<(), LayerStackError> {
        remove_path(destination)?;
        std::fs::create_dir_all(destination)?;
        for layer in manifest.layers.iter().rev() {
            apply_layer(&self.layer_dir(layer)?, destination)?;
        }
        Ok(())
    }

    fn layer_dir(&self, layer: &LayerRef) -> Result<PathBuf, LayerStackError> {
        validate_layer_ref(layer)?;
        let path = resolve_layer_path(&self.storage_root, &layer.path);
        if !path.is_dir() {
            return Err(LayerStackError::Storage(format!(
                "manifest references missing layer {}: {}",
                layer.layer_id, layer.path
            )));
        }
        Ok(path)
    }

    fn is_whiteouted(layer_dir: &Path, rel: &str) -> bool {
        let target = join_layer_path(layer_dir, rel);
        is_kernel_whiteout(&target) || logical_whiteout_path_for_target(&target).exists()
    }

    fn lookup_blocked_by_layer(layer_dir: &Path, rel: &str) -> bool {
        let parts: Vec<&str> = rel.split('/').collect();
        for index in 1..parts.len() {
            let ancestor = parts[..index].join("/");
            let path = join_layer_path(layer_dir, &ancestor);
            if is_kernel_whiteout(&path) {
                return true;
            }
            if let Ok(meta) = std::fs::symlink_metadata(&path) {
                if meta.is_file() || meta.file_type().is_symlink() {
                    return true;
                }
            }
            if path.join(OPAQUE_MARKER).exists() {
                return true;
            }
        }
        false
    }
}

fn read_file_limited(
    path: &Path,
    meta: &std::fs::Metadata,
    max_bytes: usize,
) -> Result<Vec<u8>, LayerStackError> {
    let limit = u64::try_from(max_bytes).unwrap_or(u64::MAX);
    if meta.len() > limit {
        return Err(LayerStackError::FileTooLarge {
            size: meta.len(),
            limit: max_bytes,
        });
    }
    let file = std::fs::File::open(path)?;
    let mut bytes = Vec::new();
    file.take(limit.saturating_add(1)).read_to_end(&mut bytes)?;
    if bytes.len() > max_bytes {
        return Err(LayerStackError::FileTooLarge {
            size: u64::try_from(bytes.len()).unwrap_or(u64::MAX),
            limit: max_bytes,
        });
    }
    Ok(bytes)
}

fn stale_layer_error(
    layer: &LayerRef,
    rel: &str,
    err: Option<&dyn std::fmt::Display>,
) -> LayerStackError {
    let detail = err.map(|err| format!(" ({err})")).unwrap_or_default();
    LayerStackError::Storage(format!(
        "layer no longer present while reading {rel}: {}{detail}",
        layer.layer_id
    ))
}

fn apply_layer(layer_dir: &Path, destination: &Path) -> Result<(), LayerStackError> {
    let mut entries = collect_project_entries(layer_dir)?;
    entries.sort_by(|left, right| left.rel.cmp(&right.rel));
    for entry in entries
        .iter()
        .filter(|entry| matches!(entry.kind, ProjectEntryKind::Opaque))
    {
        clear_directory(&destination_parent(destination, &entry.rel))?;
    }
    for entry in entries.iter().filter(|entry| {
        matches!(
            entry.kind,
            ProjectEntryKind::LogicalWhiteout | ProjectEntryKind::KernelWhiteout
        )
    }) {
        let target = match entry.kind {
            ProjectEntryKind::LogicalWhiteout => {
                let Some(name) = entry.rel.file_name().and_then(|name| name.to_str()) else {
                    continue;
                };
                destination_parent(destination, &entry.rel)
                    .join(name.trim_start_matches(LOGICAL_WHITEOUT_PREFIX))
            }
            ProjectEntryKind::KernelWhiteout => destination.join(&entry.rel),
            _ => continue,
        };
        remove_path(&target)?;
    }
    for entry in entries.into_iter().filter(|entry| {
        matches!(
            entry.kind,
            ProjectEntryKind::Directory | ProjectEntryKind::File | ProjectEntryKind::Symlink
        )
    }) {
        let target = destination.join(&entry.rel);
        match entry.kind {
            ProjectEntryKind::Directory => ensure_directory(&target)?,
            ProjectEntryKind::File | ProjectEntryKind::Symlink => {
                create_parent(&target)?;
                remove_path(&target)?;
                match entry.kind {
                    ProjectEntryKind::File => {
                        std::fs::copy(entry.path, target)?;
                    }
                    ProjectEntryKind::Symlink => {
                        let link_target = std::fs::read_link(entry.path)?;
                        std::os::unix::fs::symlink(link_target, target)?;
                    }
                    _ => {}
                }
            }
            ProjectEntryKind::Opaque
            | ProjectEntryKind::LogicalWhiteout
            | ProjectEntryKind::KernelWhiteout => {}
        }
    }
    Ok(())
}

pub(super) fn layer_has_boundary_markers(layer_dir: &Path) -> Result<bool, LayerStackError> {
    Ok(collect_project_entries(layer_dir)?
        .into_iter()
        .any(|entry| {
            matches!(
                entry.kind,
                ProjectEntryKind::Opaque
                    | ProjectEntryKind::LogicalWhiteout
                    | ProjectEntryKind::KernelWhiteout
            )
        }))
}

#[derive(Debug)]
struct ProjectEntry {
    path: PathBuf,
    rel: PathBuf,
    kind: ProjectEntryKind,
}

#[derive(Debug, Clone, Copy)]
enum ProjectEntryKind {
    Opaque,
    LogicalWhiteout,
    KernelWhiteout,
    Directory,
    File,
    Symlink,
}

fn collect_project_entries(layer_dir: &Path) -> Result<Vec<ProjectEntry>, LayerStackError> {
    let mut entries = Vec::new();
    let mut stack = vec![layer_dir.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let mut children = std::fs::read_dir(&dir)?.collect::<Result<Vec<_>, _>>()?;
        children.sort_by_key(std::fs::DirEntry::path);
        for entry in children {
            let path = entry.path();
            let rel = path
                .strip_prefix(layer_dir)
                .map_err(|err| LayerStackError::Storage(err.to_string()))?
                .to_path_buf();
            let file_type = entry.file_type()?;
            let name = path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or_default();
            let meta = std::fs::symlink_metadata(&path)?;
            let kind = if name == OPAQUE_MARKER {
                ProjectEntryKind::Opaque
            } else if name.starts_with(LOGICAL_WHITEOUT_PREFIX) {
                ProjectEntryKind::LogicalWhiteout
            } else if is_kernel_whiteout_meta(&path, &meta) {
                ProjectEntryKind::KernelWhiteout
            } else if file_type.is_symlink() {
                ProjectEntryKind::Symlink
            } else if file_type.is_dir() {
                stack.push(path.clone());
                ProjectEntryKind::Directory
            } else if file_type.is_file() {
                ProjectEntryKind::File
            } else {
                continue;
            };
            entries.push(ProjectEntry { path, rel, kind });
        }
    }
    Ok(entries)
}

fn destination_parent(destination: &Path, rel: &Path) -> PathBuf {
    rel.parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .map_or_else(
            || destination.to_path_buf(),
            |parent| destination.join(parent),
        )
}

fn create_parent(path: &Path) -> Result<(), LayerStackError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(())
}

fn clear_directory(path: &Path) -> Result<(), LayerStackError> {
    ensure_directory(path)?;
    for entry in std::fs::read_dir(path)? {
        remove_path(&entry?.path())?;
    }
    Ok(())
}

fn ensure_directory(path: &Path) -> Result<(), LayerStackError> {
    match std::fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_symlink() || !meta.is_dir() => remove_path(path)?,
        Ok(_) => {}
        Err(err) if err.kind() == ErrorKind::NotFound => {}
        Err(err) => return Err(err.into()),
    }
    std::fs::create_dir_all(path)?;
    Ok(())
}
