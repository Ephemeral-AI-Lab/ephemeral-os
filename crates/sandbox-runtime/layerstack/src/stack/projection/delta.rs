//! Winner fold over the delta manifest: every published layer above the base,
//! folded newest-first into one per-path verdict map with `MergedView`
//! masking semantics (whiteout cuts, opaque cuts, non-directory ancestors).
//! Metadata-only: winner content is never read here; sources are layer-dir
//! paths the emit stage streams from under the export lease.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use crate::error::LayerStackError;
use crate::fs::resolve_layer_path;
use crate::model::{LayerPath, LayerRef, Manifest};
use crate::whiteout::LOGICAL_WHITEOUT_PREFIX;

use super::apply::{collect_project_entries, ProjectEntry, ProjectEntryKind};

const BASE_LAYER_PREFIX: char = 'B';

/// Per-path verdict of the newest-first fold. `File`/`Symlink`/`Directory`
/// carry the winning layer's on-disk source; `OpaqueDir` is a directory
/// winner whose opaque cut must ride the stream (it is the only record that
/// base content under the directory was masked).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeltaWinner {
    File { source: PathBuf },
    Symlink { source: PathBuf },
    Directory { source: PathBuf },
    Delete,
    OpaqueDir { source: PathBuf },
}

/// Output of [`fold_delta_winners`]: the winner map in deterministic
/// (`BTreeMap`) path order plus the delta layers it folded, newest-first.
#[derive(Debug)]
pub struct DeltaFold {
    pub winners: BTreeMap<LayerPath, DeltaWinner>,
    pub delta_layers: Vec<LayerRef>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LayerDeltaDescription {
    pub entries: Vec<LayerDeltaEntry>,
    pub truncated: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LayerDeltaEntry {
    pub path: LayerPath,
    pub kind: LayerDeltaEntryKind,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LayerDeltaEntryKind {
    File,
    Symlink,
    Directory,
    Delete,
    OpaqueDir,
}

/// The delta manifest predicate: the active layers with every base (`B*`)
/// layer removed, newest-first.
///
/// # Errors
///
/// Returns [`LayerStackError`] when the manifest carries no base layer —
/// nothing enforces exactly one base at the bottom, so a zero-base manifest
/// is refused rather than exporting a base as a delta layer.
pub fn delta_layer_refs(manifest: &Manifest) -> Result<Vec<LayerRef>, LayerStackError> {
    let mut delta = Vec::new();
    let mut bases = 0_usize;
    for layer in &manifest.layers {
        if layer.layer_id.starts_with(BASE_LAYER_PREFIX) {
            bases += 1;
        } else {
            delta.push(layer.clone());
        }
    }
    if bases == 0 {
        return Err(LayerStackError::Storage(format!(
            "manifest version {} has no base (B*) layer; refusing to export",
            manifest.version
        )));
    }
    Ok(delta)
}

/// Fold the delta layers newest-first into the winner map.
///
/// # Errors
///
/// Returns [`LayerStackError`] on a zero-base manifest, a missing layer
/// directory, an unreadable layer walk, or an opaque marker at a layer root
/// (unreachable through publish, which fail-closes `.wh.` path components).
pub fn fold_delta_winners(
    storage_root: &Path,
    manifest: &Manifest,
) -> Result<DeltaFold, LayerStackError> {
    let delta_layers = delta_layer_refs(manifest)?;
    let mut winners: BTreeMap<LayerPath, DeltaWinner> = BTreeMap::new();
    let mut cut_roots: BTreeSet<String> = BTreeSet::new();
    for layer in &delta_layers {
        let layer_dir = resolve_layer_path(storage_root, &layer.path);
        if !layer_dir.is_dir() {
            return Err(LayerStackError::Storage(format!(
                "manifest references missing layer {}: {}",
                layer.layer_id, layer.path
            )));
        }
        let entries = collect_project_entries(&layer_dir)?;
        let layer_cuts = fold_layer(&entries, &layer_dir, &mut winners, &cut_roots)?;
        cut_roots.extend(layer_cuts);
    }
    Ok(DeltaFold {
        winners,
        delta_layers,
    })
}

pub fn describe_layer_delta(
    layer_dir: &Path,
    limit: usize,
) -> Result<LayerDeltaDescription, LayerStackError> {
    let mut by_path: BTreeMap<LayerPath, LayerDeltaEntryKind> = BTreeMap::new();
    for entry in collect_project_entries(layer_dir)? {
        let Some((path, kind)) = describe_project_entry(&entry)? else {
            continue;
        };
        match (by_path.get(&path), kind) {
            (Some(LayerDeltaEntryKind::OpaqueDir), LayerDeltaEntryKind::Directory) => {}
            (_, LayerDeltaEntryKind::OpaqueDir) => {
                by_path.insert(path, kind);
            }
            (None, _) => {
                by_path.insert(path, kind);
            }
            (Some(_), _) => {}
        }
    }
    let truncated = by_path.len() > limit;
    let entries = by_path
        .into_iter()
        .take(limit)
        .map(|(path, kind)| LayerDeltaEntry { path, kind })
        .collect();
    Ok(LayerDeltaDescription { entries, truncated })
}

fn fold_layer(
    entries: &[ProjectEntry],
    layer_dir: &Path,
    winners: &mut BTreeMap<LayerPath, DeltaWinner>,
    cut_roots: &BTreeSet<String>,
) -> Result<Vec<String>, LayerStackError> {
    let mut pending_cuts: Vec<String> = Vec::new();
    for entry in entries.iter().filter(|entry| {
        matches!(
            entry.kind,
            ProjectEntryKind::LogicalWhiteout | ProjectEntryKind::KernelWhiteout
        )
    }) {
        let Some(target) = whiteout_target(entry) else {
            continue;
        };
        if ancestor_masked(cut_roots, target.as_str()) {
            continue;
        }
        pending_cuts.push(target.as_str().to_owned());
        winners.entry(target).or_insert(DeltaWinner::Delete);
    }
    for entry in entries
        .iter()
        .filter(|entry| matches!(entry.kind, ProjectEntryKind::Opaque))
    {
        let marker_rel = rel_str(&entry.rel)?;
        if ancestor_masked(cut_roots, &marker_rel) {
            continue;
        }
        let dir = entry
            .rel
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty());
        let Some(dir) = dir else {
            return Err(LayerStackError::Storage(format!(
                "opaque marker at layer root is not exportable: {}",
                layer_dir.display()
            )));
        };
        let dir = LayerPath::parse(&rel_str(dir)?)?;
        match winners.get(&dir) {
            None => {
                pending_cuts.push(dir.as_str().to_owned());
                winners.insert(
                    dir,
                    DeltaWinner::OpaqueDir {
                        source: entry
                            .path
                            .parent()
                            .map_or_else(|| layer_dir.to_path_buf(), Path::to_path_buf),
                    },
                );
            }
            Some(DeltaWinner::Directory { source }) => {
                let source = source.clone();
                pending_cuts.push(dir.as_str().to_owned());
                winners.insert(dir, DeltaWinner::OpaqueDir { source });
            }
            Some(_) => {}
        }
    }
    for entry in entries.iter().filter(|entry| {
        matches!(
            entry.kind,
            ProjectEntryKind::Directory | ProjectEntryKind::File | ProjectEntryKind::Symlink
        )
    }) {
        let rel = rel_str(&entry.rel)?;
        if ancestor_masked(cut_roots, &rel) {
            continue;
        }
        let path = LayerPath::parse(&rel)?;
        match entry.kind {
            ProjectEntryKind::File => {
                pending_cuts.push(rel);
                winners.entry(path).or_insert(DeltaWinner::File {
                    source: entry.path.clone(),
                });
            }
            ProjectEntryKind::Symlink => {
                pending_cuts.push(rel);
                winners.entry(path).or_insert(DeltaWinner::Symlink {
                    source: entry.path.clone(),
                });
            }
            ProjectEntryKind::Directory => {
                winners.entry(path).or_insert(DeltaWinner::Directory {
                    source: entry.path.clone(),
                });
            }
            _ => {}
        }
    }
    Ok(pending_cuts)
}

fn describe_project_entry(
    entry: &ProjectEntry,
) -> Result<Option<(LayerPath, LayerDeltaEntryKind)>, LayerStackError> {
    match entry.kind {
        ProjectEntryKind::LogicalWhiteout | ProjectEntryKind::KernelWhiteout => {
            Ok(whiteout_target(entry).map(|path| (path, LayerDeltaEntryKind::Delete)))
        }
        ProjectEntryKind::Opaque => {
            let Some(parent) = entry
                .rel
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
            else {
                return Ok(None);
            };
            Ok(Some((
                LayerPath::parse(&rel_str(parent)?)?,
                LayerDeltaEntryKind::OpaqueDir,
            )))
        }
        ProjectEntryKind::Directory => Ok(Some((
            LayerPath::parse(&rel_str(&entry.rel)?)?,
            LayerDeltaEntryKind::Directory,
        ))),
        ProjectEntryKind::File => Ok(Some((
            LayerPath::parse(&rel_str(&entry.rel)?)?,
            LayerDeltaEntryKind::File,
        ))),
        ProjectEntryKind::Symlink => Ok(Some((
            LayerPath::parse(&rel_str(&entry.rel)?)?,
            LayerDeltaEntryKind::Symlink,
        ))),
    }
}

fn whiteout_target(entry: &ProjectEntry) -> Option<LayerPath> {
    match entry.kind {
        ProjectEntryKind::LogicalWhiteout => {
            let name = entry.rel.file_name()?.to_str()?;
            let stripped = name.strip_prefix(LOGICAL_WHITEOUT_PREFIX)?;
            if stripped.is_empty() || stripped.starts_with(LOGICAL_WHITEOUT_PREFIX) {
                return None;
            }
            let target = entry
                .rel
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
                .map_or_else(|| PathBuf::from(stripped), |parent| parent.join(stripped));
            LayerPath::parse(target.to_str()?).ok()
        }
        ProjectEntryKind::KernelWhiteout => LayerPath::parse(entry.rel.to_str()?).ok(),
        _ => None,
    }
}

fn ancestor_masked(cut_roots: &BTreeSet<String>, rel: &str) -> bool {
    let mut end = 0_usize;
    for part in rel.split('/') {
        let next = end + part.len();
        if next >= rel.len() {
            break;
        }
        if cut_roots.contains(&rel[..next]) {
            return true;
        }
        end = next + 1;
    }
    false
}

fn rel_str(rel: &Path) -> Result<String, LayerStackError> {
    rel.to_str().map(str::to_owned).ok_or_else(|| {
        LayerStackError::Storage(format!("layer entry path is not UTF-8: {}", rel.display()))
    })
}
