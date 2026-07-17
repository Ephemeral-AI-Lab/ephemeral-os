//! Publish-time changeset resolution: validate each source path against the
//! active manifest and, on a file-content mismatch, attempt a text three-way
//! merge instead of rejecting. Returns the bytes to commit plus each resolved
//! line's structural [`Origin`] — never an owner (boundary law: the runtime
//! above layerstack maps origin to an owner string after the layer commits).

use std::collections::HashMap;
use std::io::Read;

use crate::error::LayerStackError;
use crate::model::{LayerChange, LayerPath, Manifest};
use crate::stack::projection::MergedEntry;
use crate::stack::MergedView;

use super::fingerprint::content_fingerprint;
use super::merge::{three_way_merge, LineRange, MergeOutcome, Origin};
use super::model::{
    ContentFingerprint, PublishReject, PublishValidatedChangesRequest, ResolvedChangeset,
    SourceConflict,
};
use super::plan::PublishPlan;
use super::route::RouteKind;

const MERGE_MAX_BYTES: usize = 8 * 1024 * 1024;

struct MergedPath {
    bytes: Vec<u8>,
    origin: Vec<(LineRange, Origin)>,
}

enum FileBytes {
    Bytes(Vec<u8>),
    Absent,
    NonFile,
}

/// Resolve the planned changeset under the writer lock: validate source paths,
/// auto-merge file-content conflicts, and compute each committed line's origin.
/// All-resolved or a single reject — no partial changeset escapes.
pub(crate) fn resolve_publish_changes(
    view: &MergedView,
    active: &Manifest,
    request: &PublishValidatedChangesRequest,
    plan: &PublishPlan,
) -> Result<ResolvedChangeset, LayerStackError> {
    let base = &request.base.manifest;
    let merged = resolve_source_conflicts(view, base, active, plan)?;

    let mut changes = Vec::with_capacity(plan.accepted().len());
    let mut origin = Vec::new();
    for accepted in plan.accepted() {
        let path = accepted.change.path().clone();
        let is_file_write = matches!(
            accepted.change,
            LayerChange::Write { .. } | LayerChange::WriteFile { .. }
        );
        if !is_file_write {
            changes.push(accepted.change.clone());
            continue;
        }
        match accepted.route {
            RouteKind::Ignored => {
                changes.push(accepted.change.clone());
                origin.push((path, Vec::new()));
            }
            RouteKind::Source => {
                if let Some(resolved) = merged.get(&path) {
                    changes.push(LayerChange::Write {
                        path: path.clone(),
                        content: resolved.bytes.clone(),
                    });
                    origin.push((path, resolved.origin.clone()));
                } else {
                    let command = read_command_bytes(&accepted.change)?;
                    let ranges = clean_origin(view, base, &path, &command)?;
                    changes.push(accepted.change.clone());
                    origin.push((path, ranges));
                }
            }
        }
    }
    Ok(ResolvedChangeset { changes, origin })
}

/// Validate every source path against the active manifest. A clean path passes;
/// a file-content mismatch with a write at that exact path attempts a three-way
/// merge (clean → merged bytes + origin); any other mismatch rejects.
fn resolve_source_conflicts(
    view: &MergedView,
    base: &Manifest,
    active: &Manifest,
    plan: &PublishPlan,
) -> Result<HashMap<LayerPath, MergedPath>, LayerStackError> {
    let writes: HashMap<&LayerPath, &LayerChange> = plan
        .accepted()
        .iter()
        .filter(|accepted| accepted.route == RouteKind::Source)
        .filter_map(|accepted| match accepted.change {
            LayerChange::Write { .. } | LayerChange::WriteFile { .. } => {
                Some((accepted.change.path(), &accepted.change))
            }
            _ => None,
        })
        .collect();

    let mut merged = HashMap::new();
    for validation in plan.source_validations() {
        let actual = content_fingerprint(view, active, &validation.path)?;
        if actual == validation.expected {
            continue;
        }
        let Some(change) = writes.get(&validation.path) else {
            return Err(source_conflict(
                &validation.path,
                &validation.expected,
                actual,
            ));
        };
        let command = read_command_bytes(change)?;
        match merge_path(view, base, active, &validation.path, &command)? {
            Some(resolved) => {
                merged.insert(validation.path.clone(), resolved);
            }
            None => {
                return Err(source_conflict(
                    &validation.path,
                    &validation.expected,
                    actual,
                ))
            }
        }
    }
    Ok(merged)
}

/// Three-way merge of a concurrently-modified file. `None` is an ineligible or
/// conflicting merge (the caller rejects with `SourceConflict`).
fn merge_path(
    view: &MergedView,
    base: &Manifest,
    active: &Manifest,
    path: &LayerPath,
    command: &[u8],
) -> Result<Option<MergedPath>, LayerStackError> {
    let base_bytes = match read_file(view, base, path)? {
        FileBytes::Bytes(bytes) => bytes,
        FileBytes::Absent => Vec::new(),
        FileBytes::NonFile => return Ok(None),
    };
    let active_bytes = match read_file(view, active, path)? {
        FileBytes::Bytes(bytes) => bytes,
        FileBytes::Absent => Vec::new(),
        FileBytes::NonFile => return Ok(None),
    };
    match three_way_merge(&base_bytes, &active_bytes, command) {
        MergeOutcome::Clean { bytes, origin } => Ok(Some(MergedPath { bytes, origin })),
        MergeOutcome::Conflict | MergeOutcome::Ineligible => Ok(None),
    }
}

/// Structural origin of a clean source write (no concurrent change): the active
/// side equals the base, so this is `diff(base, command)` — net-changed lines
/// are `Command`, untouched lines `Active`. Non-text or oversized → wholesale
/// (empty range list).
fn clean_origin(
    view: &MergedView,
    base: &Manifest,
    path: &LayerPath,
    command: &[u8],
) -> Result<Vec<(LineRange, Origin)>, LayerStackError> {
    let base_bytes = match read_file(view, base, path)? {
        FileBytes::Bytes(bytes) => bytes,
        FileBytes::Absent => Vec::new(),
        FileBytes::NonFile => return Ok(Vec::new()),
    };
    match three_way_merge(&base_bytes, &base_bytes, command) {
        MergeOutcome::Clean { origin, .. } => Ok(origin),
        MergeOutcome::Conflict | MergeOutcome::Ineligible => Ok(Vec::new()),
    }
}

fn read_file(
    view: &MergedView,
    manifest: &Manifest,
    path: &LayerPath,
) -> Result<FileBytes, LayerStackError> {
    match view.read_entry_limited(path.as_str(), manifest, MERGE_MAX_BYTES) {
        Ok(MergedEntry::File { bytes }) => Ok(FileBytes::Bytes(bytes)),
        Ok(MergedEntry::Absent) => Ok(FileBytes::Absent),
        Ok(MergedEntry::Symlink { .. } | MergedEntry::Directory) => Ok(FileBytes::NonFile),
        Err(LayerStackError::FileTooLarge { .. }) => Ok(FileBytes::NonFile),
        Err(error) => Err(error),
    }
}

fn read_command_bytes(change: &LayerChange) -> Result<Vec<u8>, LayerStackError> {
    match change {
        LayerChange::Write { content, .. } => Ok(content.clone()),
        LayerChange::WriteFile { source_path, .. } => {
            let file = std::fs::File::open(source_path)?;
            let mut bytes = Vec::new();
            file.take(MERGE_MAX_BYTES as u64 + 1)
                .read_to_end(&mut bytes)?;
            Ok(bytes)
        }
        LayerChange::Delete { .. }
        | LayerChange::Symlink { .. }
        | LayerChange::Directory { .. }
        | LayerChange::OpaqueDir { .. } => Ok(Vec::new()),
    }
}

fn source_conflict(
    path: &LayerPath,
    expected: &ContentFingerprint,
    actual: ContentFingerprint,
) -> LayerStackError {
    LayerStackError::PublishRejected(Box::new(PublishReject::source_conflict(SourceConflict {
        path: path.clone(),
        expected: expected.clone(),
        actual,
    })))
}
