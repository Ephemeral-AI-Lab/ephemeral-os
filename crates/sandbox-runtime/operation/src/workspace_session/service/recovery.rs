use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
#[cfg(unix)]
use std::os::unix::ffi::OsStrExt;
#[cfg(windows)]
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};

use serde_json::json;
use sha2::{Digest, Sha256};

use crate::workspace_crate::{WorkspaceHolderIdentity, WorkspaceSessionId};

const RECOVERY_ARTIFACT_MAX_BYTES: u64 = 1024 * 1024;
const RECOVERY_MANIFEST_RESERVE_BYTES: u64 = 16 * 1024;
const RECOVERY_CONTENT_MAX_BYTES: u64 =
    RECOVERY_ARTIFACT_MAX_BYTES - RECOVERY_MANIFEST_RESERVE_BYTES;
const RECOVERY_ENTRY_MAX: usize = 1024;
const RECOVERY_DEPTH_MAX: usize = 32;
const RECOVERY_MANIFEST_READ_MAX: u64 = RECOVERY_MANIFEST_RESERVE_BYTES;

#[derive(Default)]
struct CopyState {
    copied_bytes: u64,
    visited_entries: usize,
    copied_files: usize,
    copied_directories: usize,
    observed_symlinks: usize,
    observed_special: usize,
    truncated: bool,
    source_missing: bool,
}

struct RecoveryArtifactIdentity {
    artifact_key: String,
    holder_generation: u64,
    holder_identity_sha256: String,
    source_upperdir_sha256: String,
}

pub(crate) fn preserve_recovery_artifact(
    recovery_root: &Path,
    workspace_session_id: &WorkspaceSessionId,
    holder_identity: &WorkspaceHolderIdentity,
    upperdir: &Path,
) -> Result<PathBuf, String> {
    fs::create_dir_all(recovery_root)
        .map_err(|error| format!("create recovery root {}: {error}", recovery_root.display()))?;
    let identity = recovery_artifact_identity(workspace_session_id, holder_identity, upperdir);
    let artifact = recovery_root.join(&identity.artifact_key);
    if artifact.exists() {
        validate_existing_artifact(&artifact, workspace_session_id, &identity)?;
        return Ok(artifact);
    }

    let temporary = recovery_root.join(format!(
        ".{}.tmp-{}",
        identity.artifact_key,
        std::process::id()
    ));
    if temporary.exists() {
        return Err(format!(
            "incomplete recovery transaction already exists at {}",
            temporary.display()
        ));
    }
    fs::create_dir(&temporary).map_err(|error| {
        format!(
            "create recovery transaction {}: {error}",
            temporary.display()
        )
    })?;

    let result = (|| {
        let files_root = temporary.join("files");
        fs::create_dir(&files_root).map_err(|error| {
            format!(
                "create recovery content root {}: {error}",
                files_root.display()
            )
        })?;
        let mut state = CopyState::default();
        if upperdir.exists() {
            copy_tree_bounded(upperdir, &files_root, 0, &mut state)?;
        } else {
            state.source_missing = true;
        }

        let manifest = json!({
            "schema_version": 2,
            "workspace_session_id": workspace_session_id.0,
            "holder_generation": identity.holder_generation,
            "holder_identity_sha256": identity.holder_identity_sha256,
            "source_upperdir": upperdir,
            "source_upperdir_sha256": identity.source_upperdir_sha256,
            "artifact_max_bytes": RECOVERY_ARTIFACT_MAX_BYTES,
            "content_max_bytes": RECOVERY_CONTENT_MAX_BYTES,
            "entry_max": RECOVERY_ENTRY_MAX,
            "depth_max": RECOVERY_DEPTH_MAX,
            "copied_bytes": state.copied_bytes,
            "visited_entries": state.visited_entries,
            "copied_files": state.copied_files,
            "copied_directories": state.copied_directories,
            "observed_symlinks": state.observed_symlinks,
            "observed_special": state.observed_special,
            "source_missing": state.source_missing,
            "truncated": state.truncated,
            "finalization_state": "finalization_failed",
        });
        let encoded = serde_json::to_vec_pretty(&manifest)
            .map_err(|error| format!("encode recovery manifest: {error}"))?;
        if encoded.len() as u64 > RECOVERY_MANIFEST_RESERVE_BYTES {
            return Err("recovery manifest exceeded its reserved bound".to_owned());
        }
        let manifest_path = temporary.join("manifest.json");
        let mut manifest_file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&manifest_path)
            .map_err(|error| format!("create {}: {error}", manifest_path.display()))?;
        manifest_file
            .write_all(&encoded)
            .and_then(|()| manifest_file.sync_all())
            .map_err(|error| format!("persist {}: {error}", manifest_path.display()))?;
        sync_directory(&temporary)?;
        fs::rename(&temporary, &artifact).map_err(|error| {
            format!(
                "commit recovery artifact {} -> {}: {error}",
                temporary.display(),
                artifact.display()
            )
        })?;
        sync_directory(recovery_root)?;
        Ok(artifact.clone())
    })();

    if result.is_err() {
        let _ = fs::remove_dir_all(&temporary);
    }
    result
}

fn copy_tree_bounded(
    source: &Path,
    destination: &Path,
    depth: usize,
    state: &mut CopyState,
) -> Result<(), String> {
    if depth >= RECOVERY_DEPTH_MAX {
        state.truncated = true;
        return Ok(());
    }
    let mut entries = fs::read_dir(source)
        .map_err(|error| format!("read recovery source {}: {error}", source.display()))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("read recovery source entry {}: {error}", source.display()))?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        if state.visited_entries >= RECOVERY_ENTRY_MAX {
            state.truncated = true;
            break;
        }
        state.visited_entries += 1;
        let file_type = entry.file_type().map_err(|error| {
            format!(
                "inspect recovery source {}: {error}",
                entry.path().display()
            )
        })?;
        let destination_entry = destination.join(entry.file_name());
        if file_type.is_dir() {
            fs::create_dir(&destination_entry).map_err(|error| {
                format!(
                    "create recovery directory {}: {error}",
                    destination_entry.display()
                )
            })?;
            state.copied_directories += 1;
            copy_tree_bounded(&entry.path(), &destination_entry, depth + 1, state)?;
        } else if file_type.is_file() {
            copy_file_bounded(&entry.path(), &destination_entry, state)?;
        } else if file_type.is_symlink() {
            state.observed_symlinks += 1;
            state.truncated = true;
        } else {
            state.observed_special += 1;
            state.truncated = true;
        }
    }
    Ok(())
}

fn copy_file_bounded(
    source: &Path,
    destination: &Path,
    state: &mut CopyState,
) -> Result<(), String> {
    let remaining = RECOVERY_CONTENT_MAX_BYTES.saturating_sub(state.copied_bytes);
    if remaining == 0 {
        state.truncated = true;
        return Ok(());
    }
    let source_file = File::open(source)
        .map_err(|error| format!("open recovery source {}: {error}", source.display()))?;
    let source_len = source_file
        .metadata()
        .map_err(|error| format!("stat recovery source {}: {error}", source.display()))?
        .len();
    let mut limited = source_file.take(remaining);
    let mut destination_file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(destination)
        .map_err(|error| format!("create recovery file {}: {error}", destination.display()))?;
    let copied = std::io::copy(&mut limited, &mut destination_file)
        .map_err(|error| format!("copy recovery file {}: {error}", source.display()))?;
    destination_file
        .sync_all()
        .map_err(|error| format!("persist recovery file {}: {error}", destination.display()))?;
    state.copied_bytes = state.copied_bytes.saturating_add(copied);
    state.copied_files += 1;
    if copied < source_len {
        state.truncated = true;
    }
    Ok(())
}

fn validate_existing_artifact(
    artifact: &Path,
    workspace_session_id: &WorkspaceSessionId,
    identity: &RecoveryArtifactIdentity,
) -> Result<(), String> {
    let manifest_path = artifact.join("manifest.json");
    let mut manifest = Vec::new();
    File::open(&manifest_path)
        .map_err(|error| {
            format!(
                "open existing recovery artifact {}: {error}",
                manifest_path.display()
            )
        })?
        .take(RECOVERY_MANIFEST_READ_MAX + 1)
        .read_to_end(&mut manifest)
        .map_err(|error| {
            format!(
                "read existing recovery artifact {}: {error}",
                manifest_path.display()
            )
        })?;
    if manifest.len() as u64 > RECOVERY_MANIFEST_READ_MAX {
        return Err(format!(
            "existing recovery manifest {} is oversized",
            manifest_path.display()
        ));
    }
    let decoded: serde_json::Value = serde_json::from_slice(&manifest).map_err(|error| {
        format!(
            "decode existing recovery artifact {}: {error}",
            manifest_path.display()
        )
    })?;
    let correct_owner = decoded
        .get("schema_version")
        .and_then(serde_json::Value::as_u64)
        == Some(2)
        && decoded
            .get("workspace_session_id")
            .and_then(serde_json::Value::as_str)
            == Some(workspace_session_id.0.as_str())
        && decoded
            .get("holder_generation")
            .and_then(serde_json::Value::as_u64)
            == Some(identity.holder_generation)
        && decoded
            .get("holder_identity_sha256")
            .and_then(serde_json::Value::as_str)
            == Some(identity.holder_identity_sha256.as_str())
        && decoded
            .get("source_upperdir_sha256")
            .and_then(serde_json::Value::as_str)
            == Some(identity.source_upperdir_sha256.as_str());
    if !correct_owner {
        return Err(format!(
            "existing recovery artifact {} has the wrong generation, holder identity, or source",
            artifact.display()
        ));
    }
    Ok(())
}

fn recovery_artifact_identity(
    workspace_session_id: &WorkspaceSessionId,
    holder_identity: &WorkspaceHolderIdentity,
    upperdir: &Path,
) -> RecoveryArtifactIdentity {
    let holder_identity_digest = holder_identity_digest(holder_identity);
    let source_upperdir_digest = Sha256::digest(path_identity_bytes(upperdir));
    let mut artifact_digest = Sha256::new();
    artifact_digest.update(b"eos-workspace-recovery-v2\0");
    update_len_prefixed(&mut artifact_digest, workspace_session_id.0.as_bytes());
    artifact_digest.update(holder_identity.generation.to_be_bytes());
    artifact_digest.update(holder_identity_digest);
    artifact_digest.update(source_upperdir_digest);
    RecoveryArtifactIdentity {
        artifact_key: hex_bytes(&artifact_digest.finalize()),
        holder_generation: holder_identity.generation,
        holder_identity_sha256: hex_bytes(&holder_identity_digest),
        source_upperdir_sha256: hex_bytes(&source_upperdir_digest),
    }
}

fn holder_identity_digest(identity: &WorkspaceHolderIdentity) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(b"eos-holder-identity-v1\0");
    digest.update(identity.pid.to_be_bytes());
    digest.update(identity.parent_pid.to_be_bytes());
    digest.update(identity.start_time_ticks.to_be_bytes());
    update_len_prefixed(&mut digest, &path_identity_bytes(&identity.executable));
    digest.update(identity.generation.to_be_bytes());
    digest.update([u8::from(identity.pidfd_available)]);
    digest.finalize().into()
}

fn update_len_prefixed(digest: &mut Sha256, bytes: &[u8]) {
    digest.update(u64::try_from(bytes.len()).unwrap_or(u64::MAX).to_be_bytes());
    digest.update(bytes);
}

#[cfg(unix)]
fn path_identity_bytes(path: &Path) -> Vec<u8> {
    path.as_os_str().as_bytes().to_vec()
}

#[cfg(windows)]
fn path_identity_bytes(path: &Path) -> Vec<u8> {
    path.as_os_str()
        .encode_wide()
        .flat_map(u16::to_le_bytes)
        .collect()
}

#[cfg(not(any(unix, windows)))]
fn path_identity_bytes(path: &Path) -> Vec<u8> {
    path.to_string_lossy().as_bytes().to_vec()
}

fn sync_directory(path: &Path) -> Result<(), String> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|error| format!("persist recovery directory {}: {error}", path.display()))
}

fn hex_bytes(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
