use std::io::Read;

use crate::commit::git_metadata::{is_canonical_loose_object_path, parts_after_git_dir};
use crate::model::{LayerChange, LayerPath};
use crate::{Manifest, MergedView};

use super::super::error::CommitError;
use super::model::{
    publish_decision, rejected_drop_decision, PublishDecision, Route, RouteDropReason,
};
use super::snapshot::snapshot_base_hash;

pub(super) fn command_git_metadata_decision(
    view: &MergedView,
    manifest: &Manifest,
    change: &LayerChange,
) -> Result<PublishDecision, CommitError> {
    let path = change.path();
    let Some(parts) = parts_after_git_dir(path) else {
        return Err(CommitError::RoutePreparation(format!(
            "expected git metadata path: {}",
            path.as_str()
        )));
    };

    if parts.is_empty() {
        return Ok(rejected_drop_decision(
            path.clone(),
            RouteDropReason::GitMetadataOpaqueReplace,
        ));
    }
    if let Some(reason) = restricted_git_metadata_reason(&parts) {
        return Ok(rejected_drop_decision(path.clone(), reason));
    }

    match change {
        LayerChange::Delete { .. } => Ok(rejected_drop_decision(
            path.clone(),
            RouteDropReason::GitMetadataDelete,
        )),
        LayerChange::OpaqueDir { .. } => Ok(rejected_drop_decision(
            path.clone(),
            RouteDropReason::GitMetadataOpaqueReplace,
        )),
        LayerChange::Symlink { .. } => Ok(rejected_drop_decision(
            path.clone(),
            RouteDropReason::GitMetadataUnsupported,
        )),
        LayerChange::Write { .. } | LayerChange::WriteFile { .. } => {
            command_git_metadata_write_decision(view, manifest, change, &parts)
        }
    }
}

fn command_git_metadata_write_decision(
    view: &MergedView,
    manifest: &Manifest,
    change: &LayerChange,
    parts: &[&str],
) -> Result<PublishDecision, CommitError> {
    let path = change.path();
    if parts == ["index"] {
        return git_index_write_decision(view, manifest, change);
    }
    if parts.first() == Some(&"logs") {
        return git_reflog_write_decision(view, manifest, change);
    }
    if parts.first() == Some(&"objects") {
        return git_object_write_decision(view, manifest, change, parts);
    }
    if is_git_ref_path(parts) {
        return Ok(rejected_drop_decision(
            path.clone(),
            RouteDropReason::GitRefWrite,
        ));
    }
    if is_git_operation_message_path(parts) {
        return gated_git_metadata_decision(view, manifest, change);
    }
    Ok(rejected_drop_decision(
        path.clone(),
        RouteDropReason::GitMetadataUnsupported,
    ))
}

fn git_index_write_decision(
    view: &MergedView,
    manifest: &Manifest,
    change: &LayerChange,
) -> Result<PublishDecision, CommitError> {
    let path = change.path();
    let new_bytes = change_write_bytes(change)?;
    let (base_bytes, base_exists) = snapshot_bytes_for_path(view, manifest, path)?;
    if git_index_semantically_unchanged(base_bytes.as_deref(), base_exists, &new_bytes) {
        return Ok(publish_decision(
            path.clone(),
            Route::Drop,
            None,
            Some(RouteDropReason::GitIndexStatRefresh),
        ));
    }
    Ok(rejected_drop_decision(
        path.clone(),
        RouteDropReason::GitIndexStagedState,
    ))
}

fn git_reflog_write_decision(
    view: &MergedView,
    manifest: &Manifest,
    change: &LayerChange,
) -> Result<PublishDecision, CommitError> {
    let path = change.path();
    let new_bytes = change_write_bytes(change)?;
    let (base_bytes, base_exists) = snapshot_bytes_for_path(view, manifest, path)?;
    if reflog_write_is_append_only(base_bytes.as_deref(), base_exists, &new_bytes) {
        return gated_git_metadata_decision(view, manifest, change);
    }
    Ok(rejected_drop_decision(
        path.clone(),
        RouteDropReason::GitReflogRewrite,
    ))
}

fn git_object_write_decision(
    view: &MergedView,
    manifest: &Manifest,
    change: &LayerChange,
    parts: &[&str],
) -> Result<PublishDecision, CommitError> {
    let path = change.path();
    if !is_canonical_loose_object_path(parts) {
        return Ok(rejected_drop_decision(
            path.clone(),
            RouteDropReason::GitMetadataUnsupported,
        ));
    }
    let new_bytes = change_write_bytes(change)?;
    let (base_bytes, base_exists) = snapshot_bytes_for_path(view, manifest, path)?;
    if !base_exists || base_bytes.as_deref() == Some(new_bytes.as_slice()) {
        return gated_git_metadata_decision(view, manifest, change);
    }
    Ok(rejected_drop_decision(
        path.clone(),
        RouteDropReason::GitObjectRewrite,
    ))
}

fn reflog_write_is_append_only(
    base_bytes: Option<&[u8]>,
    base_exists: bool,
    new_bytes: &[u8],
) -> bool {
    if !base_exists {
        return !new_bytes.is_empty() && new_bytes.ends_with(b"\n");
    }
    let Some(base) = base_bytes else {
        return false;
    };
    if base == new_bytes {
        return true;
    }
    new_bytes.starts_with(base) && base.ends_with(b"\n") && new_bytes.ends_with(b"\n")
}

fn gated_git_metadata_decision(
    view: &MergedView,
    manifest: &Manifest,
    change: &LayerChange,
) -> Result<PublishDecision, CommitError> {
    Ok(publish_decision(
        change.path().clone(),
        Route::Gated,
        snapshot_base_hash(view, manifest, change)?,
        None,
    ))
}

fn change_write_bytes(change: &LayerChange) -> Result<Vec<u8>, CommitError> {
    match change {
        LayerChange::Write { content, .. } => Ok(content.clone()),
        LayerChange::WriteFile {
            source_path, size, ..
        } => {
            let max = usize::try_from(*size).map_err(|_| {
                CommitError::RoutePreparation("git metadata payload too large".to_owned())
            })?;
            let mut file = std::fs::File::open(source_path).map_err(|err| {
                CommitError::RoutePreparation(format!(
                    "read git metadata payload {}: {err}",
                    source_path.display()
                ))
            })?;
            let mut bytes = Vec::with_capacity(max);
            file.read_to_end(&mut bytes).map_err(|err| {
                CommitError::RoutePreparation(format!(
                    "read git metadata payload {}: {err}",
                    source_path.display()
                ))
            })?;
            if u64::try_from(bytes.len()).unwrap_or(u64::MAX) != *size {
                return Err(CommitError::RoutePreparation(format!(
                    "git metadata payload size changed while routing {}",
                    source_path.display()
                )));
            }
            Ok(bytes)
        }
        LayerChange::Delete { .. }
        | LayerChange::Symlink { .. }
        | LayerChange::OpaqueDir { .. } => Err(CommitError::RoutePreparation(format!(
            "expected git metadata write for {}",
            change.path().as_str()
        ))),
    }
}

fn snapshot_bytes_for_path(
    view: &MergedView,
    manifest: &Manifest,
    path: &LayerPath,
) -> Result<(Option<Vec<u8>>, bool), CommitError> {
    view.read_bytes(path.as_str(), manifest)
        .map_err(|err| CommitError::RoutePreparation(err.to_string()))
}

fn git_index_semantically_unchanged(
    base_bytes: Option<&[u8]>,
    base_exists: bool,
    new_bytes: &[u8],
) -> bool {
    if base_exists && base_bytes == Some(new_bytes) {
        return true;
    }

    let Some(new_index) = parse_git_index_semantic(new_bytes) else {
        return false;
    };
    match (base_exists, base_bytes.and_then(parse_git_index_semantic)) {
        (false, _) => new_index.entries.is_empty(),
        (true, Some(base_index)) => new_index == base_index,
        (true, None) => false,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct GitIndexSemantic {
    entries: Vec<GitIndexEntrySemantic>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct GitIndexEntrySemantic {
    path: Vec<u8>,
    mode: u32,
    object_id: [u8; 20],
    flags: u16,
    extended_flags: Option<u16>,
}

fn parse_git_index_semantic(bytes: &[u8]) -> Option<GitIndexSemantic> {
    if bytes.len() < 12 || bytes.get(0..4)? != b"DIRC" {
        return None;
    }
    let version = read_be_u32(bytes.get(4..8)?)?;
    if !matches!(version, 2 | 3) {
        return None;
    }
    let entry_count = usize::try_from(read_be_u32(bytes.get(8..12)?)?).ok()?;
    let mut offset = 12_usize;
    let mut entries = Vec::with_capacity(entry_count);
    for _ in 0..entry_count {
        let entry_start = offset;
        let fixed = bytes.get(offset..offset.checked_add(62)?)?;
        let mode = read_be_u32(fixed.get(24..28)?)?;
        let object_id: [u8; 20] = fixed.get(40..60)?.try_into().ok()?;
        let raw_flags = read_be_u16(fixed.get(60..62)?)?;
        offset = offset.checked_add(62)?;
        let extended_flags = if raw_flags & 0x4000 != 0 {
            if version < 3 {
                return None;
            }
            let extended = read_be_u16(bytes.get(offset..offset.checked_add(2)?)?)?;
            offset = offset.checked_add(2)?;
            Some(extended)
        } else {
            None
        };
        let path_end =
            offset.checked_add(bytes.get(offset..)?.iter().position(|byte| *byte == 0)?)?;
        let path = bytes.get(offset..path_end)?.to_vec();
        let entry_len = path_end.checked_add(1)?.checked_sub(entry_start)?;
        let padded_len = entry_len.checked_add((8 - (entry_len % 8)) % 8)?;
        offset = entry_start.checked_add(padded_len)?;
        if offset > bytes.len() {
            return None;
        }
        entries.push(GitIndexEntrySemantic {
            path,
            mode,
            object_id,
            flags: raw_flags & 0xf000,
            extended_flags,
        });
    }
    Some(GitIndexSemantic { entries })
}

fn read_be_u32(bytes: &[u8]) -> Option<u32> {
    Some(u32::from_be_bytes(bytes.try_into().ok()?))
}

fn read_be_u16(bytes: &[u8]) -> Option<u16> {
    Some(u16::from_be_bytes(bytes.try_into().ok()?))
}

fn is_git_lock_path(parts: &[&str]) -> bool {
    parts.last().is_some_and(|part| part.ends_with(".lock"))
}

fn is_git_hook_path(parts: &[&str]) -> bool {
    parts.first() == Some(&"hooks")
}

fn restricted_git_metadata_reason(parts: &[&str]) -> Option<RouteDropReason> {
    if is_git_lock_path(parts) {
        return Some(RouteDropReason::GitLockFile);
    }
    if is_git_hook_path(parts) {
        return Some(RouteDropReason::GitHookWrite);
    }
    if is_incomplete_git_operation_path(parts) {
        return Some(RouteDropReason::GitIncompleteOperation);
    }
    None
}

fn is_git_ref_path(parts: &[&str]) -> bool {
    parts.first() == Some(&"refs") || parts == ["packed-refs"]
}

fn is_git_operation_message_path(parts: &[&str]) -> bool {
    matches!(parts, ["COMMIT_EDITMSG"] | ["MERGE_MSG"] | ["SQUASH_MSG"])
}

fn is_incomplete_git_operation_path(parts: &[&str]) -> bool {
    if let Some(first) = parts.first() {
        if matches!(*first, "sequencer" | "rebase-merge" | "rebase-apply") {
            return true;
        }
    }
    matches!(
        parts,
        ["CHERRY_PICK_HEAD"]
            | ["REVERT_HEAD"]
            | ["MERGE_HEAD"]
            | ["REBASE_HEAD"]
            | ["AUTO_MERGE"]
            | ["MERGE_AUTOSTASH"]
            | ["MERGE_MODE"]
            | ["MERGE_RR"]
            | ["BISECT_HEAD"]
            | ["BISECT_LOG"]
            | ["BISECT_NAMES"]
            | ["BISECT_START"]
            | ["BISECT_TERMS"]
    )
}
