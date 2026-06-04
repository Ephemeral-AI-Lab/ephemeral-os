use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::Instant;

use eos_protocol::Intent;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::capture::CapturedUpperdir;
use crate::timings::EphemeralTimings;

/// Agent identity supplied by the daemon for one fresh operation.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct AgentId(pub String);

/// Tool invocation identity supplied by the daemon for one fresh operation.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct InvocationId(pub String);

/// Root of the LayerStack workspace whose snapshot is used by the operation.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct WorkspaceRoot(pub PathBuf);

/// Snapshot lease material needed to mount a fresh overlay.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EphemeralSnapshot {
    pub lease_id: String,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub layer_paths: Vec<PathBuf>,
}

/// Fresh writable paths allocated for one operation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EphemeralRunDirs {
    pub run_dir: PathBuf,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
    pub output_path: PathBuf,
    pub final_path: PathBuf,
    pub request_path: Option<PathBuf>,
    pub result_path: Option<PathBuf>,
}

/// Resolved fresh workspace passed to runner/capture/finalize helpers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EphemeralWorkspace {
    pub layer_stack_root: WorkspaceRoot,
    pub workspace_root: PathBuf,
    pub agent_id: AgentId,
    pub invocation_id: InvocationId,
    pub snapshot: EphemeralSnapshot,
    pub dirs: EphemeralRunDirs,
}

/// Runner-facing tool specification for a fresh namespace call.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EphemeralToolSpec {
    pub verb: String,
    pub intent: Intent,
    pub args: Value,
    pub background: bool,
    pub timeout_seconds: Option<f64>,
}

/// Command-session metadata needed by daemon response shaping after finalize.
#[derive(Debug, Clone)]
pub struct EphemeralCommandFinalizeSpec {
    pub status: String,
    pub exit_code: i64,
    pub stdout: String,
    pub include_session_id: bool,
    pub command_session_id: Option<String>,
    pub started_at: Instant,
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

/// Combined result of a fresh run and optional publish step.
#[derive(Debug, Clone, PartialEq)]
pub struct EphemeralRunOutcome {
    pub runner: eos_runner::RunResult,
    pub capture: Option<CapturedUpperdir>,
    pub publish: Option<PublishOutcome>,
    pub timings: EphemeralTimings,
}

/// Publisher response normalized away from daemon-specific OCC result types.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PublishOutcome {
    pub status: PublishStatus,
    pub manifest_version: Option<u64>,
    pub published_paths: Vec<String>,
    pub conflicts: Vec<String>,
    pub timings: BTreeMap<String, Value>,
    pub raw: Value,
}

/// Normalized publish status for daemon response shaping.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PublishStatus {
    Published,
    NoChanges,
    Conflict,
    Rejected,
}
