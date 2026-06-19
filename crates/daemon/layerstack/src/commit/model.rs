use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::model::LayerPath;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum CommitStatus {
    #[serde(rename = "accepted")]
    Accepted,
    #[serde(rename = "committed")]
    Committed,
    #[serde(rename = "aborted_version")]
    AbortedVersion,
    #[serde(rename = "dropped")]
    Dropped,
    #[serde(rename = "failed")]
    Failed,
}

impl CommitStatus {
    #[must_use]
    pub const fn wire_str(self) -> &'static str {
        match self {
            Self::Accepted => "accepted",
            Self::Committed => "committed",
            Self::AbortedVersion => "aborted_version",
            Self::Dropped => "dropped",
            Self::Failed => "failed",
        }
    }

    #[must_use]
    pub const fn is_published(self) -> bool {
        matches!(self, Self::Accepted | Self::Committed)
    }

    #[must_use]
    pub const fn is_non_conflicting(self) -> bool {
        matches!(self, Self::Accepted | Self::Committed | Self::Dropped)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileResult {
    pub path: LayerPath,
    pub status: CommitStatus,
    pub message: String,
    pub observed_version: Option<u64>,
    pub observed_state: Option<String>,
}

impl FileResult {
    #[must_use]
    pub fn conflict_message<'a>(&'a self, fallback: &'a str) -> &'a str {
        if self.message.is_empty() {
            fallback
        } else {
            self.message.as_str()
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct OccTraceEvent {
    pub module: &'static str,
    pub name: &'static str,
    pub details: Value,
}

impl OccTraceEvent {
    #[must_use]
    pub fn new(module: &'static str, name: &'static str, details: Value) -> Self {
        Self {
            module,
            name,
            details,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChangesetResult {
    pub files: Vec<FileResult>,
    pub published_manifest_version: Option<u64>,
    pub timings: BTreeMap<String, f64>,
    pub events: Vec<OccTraceEvent>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CommitOptions {
    pub auto_squash_max_depth: usize,
}

impl Default for CommitOptions {
    fn default() -> Self {
        Self {
            auto_squash_max_depth: crate::AUTO_SQUASH_MAX_DEPTH,
        }
    }
}

impl CommitOptions {
    #[must_use]
    pub fn new(auto_squash_max_depth: usize) -> Self {
        Self {
            auto_squash_max_depth: auto_squash_max_depth.max(1),
        }
    }
}

impl ChangesetResult {
    #[must_use]
    pub fn success(&self) -> bool {
        self.files.iter().all(|f| f.status.is_non_conflicting())
    }

    #[must_use]
    pub fn first_conflict(&self) -> Option<&FileResult> {
        self.files
            .iter()
            .find(|file| !file.status.is_non_conflicting())
    }

    #[must_use]
    pub fn published_paths(&self) -> Vec<String> {
        self.files
            .iter()
            .filter(|file| file.status.is_published())
            .map(|file| file.path.as_str().to_owned())
            .collect()
    }

    #[must_use]
    pub fn published_file_count(&self) -> usize {
        self.files
            .iter()
            .filter(|file| file.status.is_published())
            .count()
    }

    #[must_use]
    pub fn trace_events(&self) -> Vec<OccTraceEvent> {
        let mut events = vec![OccTraceEvent::new(
            "occ",
            "commit_started",
            json!({
                "file_count": self.files.len(),
                "gated_path_count": self.timings.get("occ.commit.gated_path_count").copied(),
                "direct_path_count": self.timings.get("occ.commit.direct_path_count").copied(),
            }),
        )];
        events.push(OccTraceEvent::new(
            "occ",
            "validate_groups_finished",
            json!({
                "file_count": self.files.len(),
                "accepted_file_count": self.status_count(CommitStatus::Accepted),
                "committed_file_count": self.status_count(CommitStatus::Committed),
                "dropped_file_count": self.status_count(CommitStatus::Dropped),
                "aborted_version_file_count": self.status_count(CommitStatus::AbortedVersion),
                "failed_file_count": self.status_count(CommitStatus::Failed),
                "duration_s": self.timings.get("occ.commit.validate_groups_s").copied(),
            }),
        ));
        events.push(OccTraceEvent::new(
            "occ",
            "commit_finished",
            json!({
                "success": self.success(),
                "published_manifest_version": self.published_manifest_version,
                "file_count": self.files.len(),
                "published_file_count": self.published_file_count(),
                "accepted_file_count": self.status_count(CommitStatus::Accepted),
                "committed_file_count": self.status_count(CommitStatus::Committed),
                "dropped_file_count": self.status_count(CommitStatus::Dropped),
                "aborted_version_file_count": self.status_count(CommitStatus::AbortedVersion),
                "failed_file_count": self.status_count(CommitStatus::Failed),
                "gated_path_count": self.timings.get("occ.commit.gated_path_count").copied(),
                "direct_path_count": self.timings.get("occ.commit.direct_path_count").copied(),
                "duration_s": self.timings.get("occ.commit.total_s").copied(),
            }),
        ));
        events.extend(self.events.clone());
        events.extend(
            self.files
                .iter()
                .filter(|file| !file.status.is_non_conflicting())
                .map(|file| {
                    OccTraceEvent::new(
                        "occ",
                        "conflict_detected",
                        json!({
                            "path": file.path.as_str(),
                            "reason": file.status.wire_str(),
                            "message": file.conflict_message(file.status.wire_str()),
                            "observed_version": file.observed_version,
                            "observed_state": file.observed_state,
                        }),
                    )
                }),
        );
        events
    }

    fn status_count(&self, status: CommitStatus) -> usize {
        self.files
            .iter()
            .filter(|file| file.status == status)
            .count()
    }
}
