use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use serde_json::json;
use sha2::{Digest, Sha256};

use crate::workspace_crate::WorkspaceSessionId;

// The manifest is fixed-size metadata and copied regular-file content gets the
// rest of one MiB. Directory fanout and recursion are independently bounded so
// sparse trees cannot turn holder cleanup into an unbounded walk.
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

pub(crate) fn preserve_recovery_artifact(
    recovery_root: &Path,
    workspace_session_id: &WorkspaceSessionId,
    upperdir: &Path,
) -> Result<PathBuf, String> {
    fs::create_dir_all(recovery_root)
        .map_err(|error| format!("create recovery root {}: {error}", recovery_root.display()))?;
    let digest = hex_digest(workspace_session_id.0.as_bytes());
    let artifact = recovery_root.join(digest);
    if artifact.exists() {
        validate_existing_artifact(&artifact, workspace_session_id)?;
        return Ok(artifact);
    }

    let temporary = recovery_root.join(format!(
        ".{}.tmp-{}",
        hex_digest(workspace_session_id.0.as_bytes()),
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
            "schema_version": 1,
            "workspace_session_id": workspace_session_id.0,
            "source_upperdir": upperdir,
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
            // Never follow or recreate an attacker-controlled symlink in a
            // durable host-side artifact. The manifest records the omission.
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
    if decoded
        .get("workspace_session_id")
        .and_then(serde_json::Value::as_str)
        != Some(workspace_session_id.0.as_str())
    {
        return Err(format!(
            "existing recovery artifact {} has the wrong owner",
            artifact.display()
        ));
    }
    Ok(())
}

fn sync_directory(path: &Path) -> Result<(), String> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|error| format!("persist recovery directory {}: {error}", path.display()))
}

fn hex_digest(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temporary_root(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "sandbox-recovery-{label}-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ))
    }

    #[test]
    fn artifact_is_bounded_durable_and_idempotent() {
        let root = temporary_root("bounded");
        let _ = fs::remove_dir_all(&root);
        let source = root.join("upper");
        fs::create_dir_all(source.join("nested")).expect("source tree");
        fs::write(
            source.join("nested/large"),
            vec![b'x'; RECOVERY_CONTENT_MAX_BYTES as usize + 4096],
        )
        .expect("large source");
        let recovery = root.join("recovery");
        let workspace = WorkspaceSessionId("workspace/unsafe".to_owned());

        let first =
            preserve_recovery_artifact(&recovery, &workspace, &source).expect("artifact commits");
        let second = preserve_recovery_artifact(&recovery, &workspace, &source)
            .expect("artifact retry validates existing commit");

        assert_eq!(first, second);
        assert!(first.join("manifest.json").is_file());
        let copied = fs::metadata(first.join("files/nested/large"))
            .expect("bounded file")
            .len();
        assert_eq!(copied, RECOVERY_CONTENT_MAX_BYTES);
        let manifest: serde_json::Value =
            serde_json::from_slice(&fs::read(first.join("manifest.json")).expect("manifest"))
                .expect("manifest json");
        assert_eq!(manifest["truncated"], true);
        assert_eq!(manifest["finalization_state"], "finalization_failed");
        assert!(!recovery.join("workspace/unsafe").exists());
        let _ = fs::remove_dir_all(&root);
    }
}
