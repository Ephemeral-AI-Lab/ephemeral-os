use std::collections::BTreeSet;
use std::io::{ErrorKind, Read};
use std::path::{Path, PathBuf};

mod apply;

use crate::error::LayerStackError;
use crate::fs::{join_layer_path, remove_path, resolve_layer_path, validate_layer_ref};
use crate::model::{LayerPath, LayerRef, Manifest};
use apply::apply_layer;

use crate::whiteout::{is_kernel_whiteout, logical_whiteout_path_for_target, OPAQUE_MARKER};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum MergedEntry {
    Absent,
    File { bytes: Vec<u8> },
    Symlink { target: String },
    Directory,
}

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
        match self.read_entry_limited(path, manifest, max_bytes)? {
            MergedEntry::Absent => return Ok((None, false)),
            MergedEntry::File { bytes } => return Ok((Some(bytes), true)),
            MergedEntry::Symlink { target } => {
                return Ok((Some(target.into_bytes()), true));
            }
            MergedEntry::Directory => {}
        }
        Err(stale_layer_error(
            manifest.layers.first().ok_or_else(|| {
                LayerStackError::Storage(format!("directory entry found while reading {path}"))
            })?,
            path,
            None,
        ))
    }

    pub(crate) fn read_entry(
        &self,
        path: &str,
        manifest: &Manifest,
    ) -> Result<MergedEntry, LayerStackError> {
        self.read_entry_limited(path, manifest, usize::MAX)
    }

    pub(crate) fn read_entry_limited(
        &self,
        path: &str,
        manifest: &Manifest,
        max_bytes: usize,
    ) -> Result<MergedEntry, LayerStackError> {
        let rel = LayerPath::parse(path)?;
        for layer in &manifest.layers {
            let layer_dir = self.layer_dir(layer)?;
            if Self::is_whiteouted(&layer_dir, rel.as_str()) {
                return Ok(MergedEntry::Absent);
            }
            let target = join_layer_path(&layer_dir, rel.as_str());
            match std::fs::symlink_metadata(&target) {
                Ok(meta) if meta.file_type().is_symlink() => {
                    let target = std::fs::read_link(&target)
                        .map_err(|err| stale_layer_error(layer, rel.as_str(), Some(&err)))?;
                    return Ok(MergedEntry::Symlink {
                        target: target.to_string_lossy().into_owned(),
                    });
                }
                Ok(meta) if meta.is_file() => {
                    let bytes = match read_file_limited(&target, &meta, max_bytes) {
                        Ok(bytes) => bytes,
                        Err(err @ LayerStackError::FileTooLarge { .. }) => return Err(err),
                        Err(err) => return Err(stale_layer_error(layer, rel.as_str(), Some(&err))),
                    };
                    return Ok(MergedEntry::File { bytes });
                }
                Ok(meta) if meta.is_dir() => return Ok(MergedEntry::Directory),
                Ok(_) => return Err(stale_layer_error(layer, rel.as_str(), None)),
                Err(err)
                    if err.kind() == ErrorKind::NotFound
                        || (err.kind() == ErrorKind::NotADirectory
                            && Self::lookup_blocked_by_layer(&layer_dir, rel.as_str())) => {}
                Err(err) => return Err(stale_layer_error(layer, rel.as_str(), Some(&err))),
            }
            if Self::lookup_blocked_by_layer(&layer_dir, rel.as_str()) {
                return Ok(MergedEntry::Absent);
            }
        }
        Ok(MergedEntry::Absent)
    }

    /// Classify one path against the active manifest for the runtime `file`
    /// domain: absent, a regular file (bytes loaded up to `max_bytes`), a
    /// non-regular entry, or oversized. `max_bytes == 0` classifies an existing
    /// regular file without loading its bytes and never reports `TooLarge`; a
    /// larger regular file over `max_bytes` reports `TooLarge` without loading.
    pub(crate) fn read_classified(
        &self,
        path: &str,
        manifest: &Manifest,
        max_bytes: usize,
    ) -> Result<crate::stack::file_read::ManifestFileRead, LayerStackError> {
        use crate::stack::file_read::ManifestFileRead;
        let rel = LayerPath::parse(path)?;
        for layer in &manifest.layers {
            let layer_dir = self.layer_dir(layer)?;
            if Self::is_whiteouted(&layer_dir, rel.as_str()) {
                return Ok(ManifestFileRead::Absent);
            }
            if Self::lookup_symlink_ancestor_by_layer(&layer_dir, rel.as_str()) {
                return Ok(ManifestFileRead::Symlink);
            }
            let target = join_layer_path(&layer_dir, rel.as_str());
            match std::fs::symlink_metadata(&target) {
                Ok(meta) if meta.file_type().is_symlink() => return Ok(ManifestFileRead::Symlink),
                Ok(meta) if meta.is_file() => {
                    let total_bytes = meta.len();
                    if max_bytes == 0 {
                        return Ok(ManifestFileRead::File {
                            bytes: Vec::new(),
                            total_bytes,
                        });
                    }
                    if total_bytes > max_bytes as u64 {
                        return Ok(ManifestFileRead::TooLarge {
                            size: total_bytes,
                            limit: max_bytes,
                        });
                    }
                    let bytes = std::fs::read(&target)
                        .map_err(|err| stale_layer_error(layer, rel.as_str(), Some(&err)))?;
                    return Ok(ManifestFileRead::File { bytes, total_bytes });
                }
                Ok(meta) if meta.is_dir() => return Ok(ManifestFileRead::Directory),
                Ok(_) => return Err(stale_layer_error(layer, rel.as_str(), None)),
                Err(err)
                    if err.kind() == ErrorKind::NotFound
                        || (err.kind() == ErrorKind::NotADirectory
                            && Self::lookup_blocked_by_layer(&layer_dir, rel.as_str())) => {}
                Err(err) => return Err(stale_layer_error(layer, rel.as_str(), Some(&err))),
            }
            if Self::lookup_blocked_by_layer(&layer_dir, rel.as_str()) {
                return Ok(ManifestFileRead::Absent);
            }
        }
        Ok(ManifestFileRead::Absent)
    }

    /// Merged one-level listing of `rel` (or the workspace root for `None`)
    /// across the layer chain: the first layer to carry a name wins, layer
    /// whiteouts hide lower entries, and an opaque directory marker cuts
    /// lower layers off. Read-only.
    pub(crate) fn list_dir(
        &self,
        rel: Option<&LayerPath>,
        manifest: &Manifest,
        limit: usize,
    ) -> Result<crate::stack::dir_list::ManifestDirList, LayerStackError> {
        use crate::stack::dir_list::{ManifestDirEntry, ManifestDirList};
        if let Some(rel) = rel {
            match self.read_entry(rel.as_str(), manifest)? {
                MergedEntry::Absent => return Ok(ManifestDirList::Absent),
                MergedEntry::File { .. } | MergedEntry::Symlink { .. } => {
                    return Ok(ManifestDirList::NotDirectory)
                }
                MergedEntry::Directory => {}
            }
        }
        let mut seen = std::collections::BTreeMap::new();
        let mut hidden: BTreeSet<String> = BTreeSet::new();
        let mut truncated = false;
        for layer in &manifest.layers {
            let layer_dir = self.layer_dir(layer)?;
            let dir = match rel {
                Some(rel) => {
                    if Self::is_whiteouted(&layer_dir, rel.as_str())
                        || Self::lookup_blocked_by_layer(&layer_dir, rel.as_str())
                    {
                        break;
                    }
                    let target = join_layer_path(&layer_dir, rel.as_str());
                    match std::fs::symlink_metadata(&target) {
                        Ok(meta) if meta.is_dir() => target,
                        Ok(_) => break,
                        Err(_) => continue,
                    }
                }
                None => layer_dir.clone(),
            };
            collect_dir_level(&dir, &mut seen, &mut hidden, limit, &mut truncated)?;
            if truncated || dir.join(OPAQUE_MARKER).exists() {
                break;
            }
        }
        let entries: Vec<ManifestDirEntry> = seen.into_values().collect();
        Ok(ManifestDirList::Entries { entries, truncated })
    }

    pub(crate) fn visible_descendants(
        &self,
        dir: &LayerPath,
        manifest: &Manifest,
        limit: usize,
    ) -> Result<Vec<LayerPath>, LayerStackError> {
        let prefix = format!("{}/", dir.as_str());
        let mut candidates = BTreeSet::new();
        for layer in &manifest.layers {
            let layer_dir = self.layer_dir(layer)?;
            let start = join_layer_path(&layer_dir, dir.as_str());
            collect_candidate_descendants(&layer_dir, &start, &prefix, limit, &mut candidates)?;
            if candidates.len() > limit {
                break;
            }
        }

        let mut visible = Vec::new();
        for path in candidates {
            match self.read_entry(path.as_str(), manifest)? {
                MergedEntry::File { .. } | MergedEntry::Symlink { .. } | MergedEntry::Directory => {
                    visible.push(path);
                    if visible.len() > limit {
                        break;
                    }
                }
                MergedEntry::Absent => {}
            }
        }
        Ok(visible)
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
            if Self::is_whiteouted(layer_dir, &ancestor) {
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

    fn lookup_symlink_ancestor_by_layer(layer_dir: &Path, rel: &str) -> bool {
        let parts: Vec<&str> = rel.split('/').collect();
        for index in 1..parts.len() {
            let ancestor = parts[..index].join("/");
            let path = join_layer_path(layer_dir, &ancestor);
            if std::fs::symlink_metadata(&path).is_ok_and(|meta| meta.file_type().is_symlink()) {
                return true;
            }
        }
        false
    }
}

fn collect_dir_level(
    dir: &Path,
    seen: &mut std::collections::BTreeMap<String, crate::stack::dir_list::ManifestDirEntry>,
    hidden: &mut BTreeSet<String>,
    limit: usize,
    truncated: &mut bool,
) -> Result<(), LayerStackError> {
    use crate::stack::dir_list::{ManifestDirEntry, ManifestDirEntryKind};
    use crate::whiteout::LOGICAL_WHITEOUT_PREFIX;
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(err) if err.kind() == ErrorKind::NotFound => return Ok(()),
        Err(err) if err.kind() == ErrorKind::NotADirectory => return Ok(()),
        Err(err) => return Err(err.into()),
    };
    for entry in entries {
        let entry = entry?;
        let Ok(name) = entry.file_name().into_string() else {
            continue;
        };
        if name == OPAQUE_MARKER {
            continue;
        }
        if let Some(target) = name.strip_prefix(LOGICAL_WHITEOUT_PREFIX) {
            hidden.insert(target.to_owned());
            continue;
        }
        let path = entry.path();
        let Ok(meta) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        if is_kernel_whiteout(&path) {
            hidden.insert(name);
            continue;
        }
        if hidden.contains(&name) || seen.contains_key(&name) {
            continue;
        }
        if seen.len() >= limit {
            *truncated = true;
            return Ok(());
        }
        let (kind, size) = if meta.file_type().is_symlink() {
            (ManifestDirEntryKind::Symlink, None)
        } else if meta.is_dir() {
            (ManifestDirEntryKind::Directory, None)
        } else if meta.is_file() {
            (ManifestDirEntryKind::File, Some(meta.len()))
        } else {
            (ManifestDirEntryKind::Other, None)
        };
        seen.insert(name.clone(), ManifestDirEntry { name, kind, size });
    }
    Ok(())
}

fn collect_candidate_descendants(
    root: &Path,
    dir: &Path,
    prefix: &str,
    limit: usize,
    candidates: &mut BTreeSet<LayerPath>,
) -> Result<(), LayerStackError> {
    if candidates.len() > limit {
        return Ok(());
    }
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(err) if err.kind() == ErrorKind::NotFound => return Ok(()),
        Err(err) if err.kind() == ErrorKind::NotADirectory => return Ok(()),
        Err(err) => return Err(err.into()),
    };
    for entry in entries {
        if candidates.len() > limit {
            return Ok(());
        }
        let entry = entry?;
        let path = entry.path();
        let rel = path
            .strip_prefix(root)
            .map_err(|err| LayerStackError::Storage(err.to_string()))?;
        let Some(rel) = rel.to_str() else {
            continue;
        };
        let meta = std::fs::symlink_metadata(&path)?;
        if meta.is_dir() {
            collect_candidate_descendants(root, &path, prefix, limit, candidates)?;
            continue;
        }
        let name = path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("");
        if name == OPAQUE_MARKER || name.starts_with(".wh.") {
            continue;
        }
        if rel.starts_with(prefix) {
            if let Ok(layer_path) = LayerPath::parse(rel) {
                candidates.insert(layer_path);
                if candidates.len() > limit {
                    return Ok(());
                }
            }
        }
    }
    Ok(())
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
