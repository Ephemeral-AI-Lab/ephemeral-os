use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::contract::ids::{CallerId, InvocationId};
use crate::contract::SnapshotLease;

/// Root of the LayerStack workspace whose snapshot is used by the operation.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct LayerStackRoot(pub PathBuf);

/// Fresh writable paths allocated for one operation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EphemeralRunDirs {
    pub run_dir: PathBuf,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
}

/// Resolved fresh workspace passed to runner/capture/finalize helpers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EphemeralWorkspace {
    pub layer_stack_root: LayerStackRoot,
    pub workspace_root: PathBuf,
    pub caller_id: CallerId,
    pub invocation_id: InvocationId,
    pub snapshot: SnapshotLease,
    pub dirs: EphemeralRunDirs,
}

/// Local path-kind classification for captured upperdir changes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PathChange {
    pub path: String,
    pub kind: PathChangeKind,
}

/// The path operation kind observed in the upperdir.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PathChangeKind {
    Write,
    Delete,
    Symlink,
    OpaqueDir,
}

/// Map captured path changes to their wire `(path, kind)` string pairs.
pub fn path_changes_to_wire(path_changes: &[PathChange]) -> Vec<(String, String)> {
    path_changes
        .iter()
        .map(|change| {
            (
                change.path.clone(),
                path_change_kind_wire(change.kind).to_owned(),
            )
        })
        .collect()
}

const fn path_change_kind_wire(kind: PathChangeKind) -> &'static str {
    match kind {
        PathChangeKind::Write => "write",
        PathChangeKind::Delete => "delete",
        PathChangeKind::Symlink => "symlink",
        PathChangeKind::OpaqueDir => "opaque_dir",
    }
}

/// Publisher response normalized away from daemon-specific OCC result types.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PublishOutcome {
    pub published_paths: Vec<String>,
    pub timings: BTreeMap<String, Value>,
    pub raw: Value,
}
