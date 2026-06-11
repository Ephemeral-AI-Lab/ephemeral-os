use std::collections::BTreeMap;

use eos_layerstack::{CommitStatus, LayerChange};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::MutationSource;

pub type WorkspaceTimings = BTreeMap<String, Value>;

pub type ChangedPathKinds = BTreeMap<String, ChangedPathKind>;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkspaceKind {
    #[default]
    Ephemeral,
    Isolated,
}

impl WorkspaceKind {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ephemeral => "ephemeral",
            Self::Isolated => "isolated",
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MutationStatus {
    Accepted,
    #[default]
    Committed,
    Rejected,
    AbortedVersion,
    AbortedOverlap,
    Dropped,
    Failed,
}

impl MutationStatus {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Accepted => "accepted",
            Self::Committed => "committed",
            Self::Rejected => "rejected",
            Self::AbortedVersion => "aborted_version",
            Self::AbortedOverlap => "aborted_overlap",
            Self::Dropped => "dropped",
            Self::Failed => "failed",
        }
    }
}

impl From<CommitStatus> for MutationStatus {
    fn from(status: CommitStatus) -> Self {
        match status {
            CommitStatus::Accepted => Self::Accepted,
            CommitStatus::Committed => Self::Committed,
            CommitStatus::AbortedVersion => Self::AbortedVersion,
            CommitStatus::Dropped => Self::Dropped,
            CommitStatus::Failed => Self::Failed,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChangedPathKind {
    Write,
    Delete,
    Symlink,
    OpaqueDir,
}

impl ChangedPathKind {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Write => "write",
            Self::Delete => "delete",
            Self::Symlink => "symlink",
            Self::OpaqueDir => "opaque_dir",
        }
    }

    #[must_use]
    pub fn from_wire_str(raw: &str) -> Option<Self> {
        match raw {
            "write" => Some(Self::Write),
            "delete" => Some(Self::Delete),
            "symlink" => Some(Self::Symlink),
            "opaque_dir" => Some(Self::OpaqueDir),
            _ => None,
        }
    }
}

impl From<&LayerChange> for ChangedPathKind {
    fn from(change: &LayerChange) -> Self {
        match change {
            LayerChange::Write { .. } => Self::Write,
            LayerChange::Delete { .. } => Self::Delete,
            LayerChange::Symlink { .. } => Self::Symlink,
            LayerChange::OpaqueDir { .. } => Self::OpaqueDir,
        }
    }
}

/// Map captured layer changes to `(path, kind)` pairs, preserving capture
/// order; collect into [`ChangedPathKinds`] when sorted-by-path is wanted.
pub(crate) fn changed_path_kind_pairs(
    changes: &[LayerChange],
) -> impl Iterator<Item = (String, ChangedPathKind)> + '_ {
    changes.iter().map(|change| {
        (
            change.path().as_str().to_owned(),
            ChangedPathKind::from(change),
        )
    })
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceConflict {
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict_file: Option<String>,
    pub message: String,
}

impl WorkspaceConflict {
    #[must_use]
    pub fn path(reason: &str, conflict_file: &str, message: &str) -> Self {
        Self {
            reason: reason.to_owned(),
            conflict_file: Some(conflict_file.to_owned()),
            message: message.to_owned(),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct MutationCore {
    pub success: bool,
    #[serde(default)]
    pub changed_paths: Vec<String>,
    #[serde(default)]
    pub changed_path_kinds: ChangedPathKinds,
    #[serde(serialize_with = "serialize_mutation_source")]
    pub mutation_source: Option<MutationSource>,
    pub conflict: Option<WorkspaceConflict>,
    pub conflict_reason: Option<String>,
    #[serde(default)]
    pub timings: WorkspaceTimings,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct WorkspaceMutationOutcome {
    #[serde(flatten)]
    pub core: MutationCore,
    #[serde(rename = "workspace")]
    pub workspace_kind: WorkspaceKind,
    pub published: bool,
    pub status: MutationStatus,
    #[serde(serialize_with = "serialize_null")]
    pub error: (),
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub applied_edits: Option<i64>,
}

fn serialize_mutation_source<S>(
    source: &Option<MutationSource>,
    serializer: S,
) -> Result<S::Ok, S::Error>
where
    S: serde::Serializer,
{
    serializer.serialize_str(source.map(MutationSource::as_str).unwrap_or(""))
}

fn serialize_null<S>(_value: &(), serializer: S) -> Result<S::Ok, S::Error>
where
    S: serde::Serializer,
{
    serializer.serialize_none()
}
