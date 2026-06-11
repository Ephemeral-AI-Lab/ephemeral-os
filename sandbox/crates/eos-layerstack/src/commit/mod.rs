use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use ignore::gitignore::GitignoreBuilder;
use ignore::Match;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::model::{LayerChange, LayerPath};
use crate::{LayerStack, LayerStackError, Manifest, MergedView};

mod worker;

pub(crate) use worker::{
    configure_auto_squash_max_depth, CommitQueue, CommitTransaction, CommitTransactionPort,
    PreparedChangeset, PublishConflict,
};
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum CommitError {
    #[error("occ commit queue is closed")]
    QueueClosed,

    #[error("occ commit queue has not been started")]
    QueueNotStarted,

    #[error("occ commit queue worker failed to start: {0}")]
    WorkerStart(String),

    #[error("occ commit queue worker panicked")]
    WorkerPanicked,

    #[error("occ commit queue state lock poisoned: {0}")]
    QueueStatePoisoned(&'static str),

    #[error("occ commit reply channel disconnected")]
    ReplyDisconnected,

    #[error("cas mismatch retry budget exhausted after {attempts} attempts")]
    CasRetryExhausted {
        attempts: u32,
    },

    #[error("occ route preparation failed: {0}")]
    RoutePreparation(String),

    #[error(transparent)]
    Cas(#[from] CasError),

    #[error(transparent)]
    Storage(#[from] crate::LayerStackError),
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[non_exhaustive]
pub enum Route {
    #[serde(rename = "gated")]
    Gated,
    #[serde(rename = "direct")]
    Direct,
    #[serde(rename = "drop")]
    Drop,
    #[serde(rename = "reject")]
    Reject,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[non_exhaustive]
pub enum CommitStatus {
    #[serde(rename = "accepted")]
    Accepted,
    #[serde(rename = "committed")]
    Committed,
    #[serde(rename = "aborted_version")]
    AbortedVersion,
    #[serde(rename = "aborted_overlap")]
    AbortedOverlap,
    #[serde(rename = "dropped")]
    Dropped,
    #[serde(rename = "rejected")]
    Rejected,
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
            Self::AbortedOverlap => "aborted_overlap",
            Self::Dropped => "dropped",
            Self::Rejected => "rejected",
            Self::Failed => "failed",
        }
    }

    #[must_use]
    pub const fn is_published(self) -> bool {
        matches!(self, Self::Accepted | Self::Committed)
    }

    #[must_use]
    pub const fn is_success(self) -> bool {
        matches!(self, Self::Accepted | Self::Committed | Self::Dropped)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishDecision {
    pub path: LayerPath,
    pub route: Route,
    pub base_hash: Option<String>,
    pub message: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileResult {
    pub path: LayerPath,
    pub status: CommitStatus,
    pub message: String,
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
pub struct ChangesetResult {
    pub files: Vec<FileResult>,
    pub published_manifest_version: Option<u64>,
    pub timings: BTreeMap<String, f64>,
}

impl ChangesetResult {
    #[must_use]
    pub fn success(&self) -> bool {
        self.files.iter().all(|f| f.status.is_success())
    }

    #[must_use]
    pub fn first_conflict(&self) -> Option<&FileResult> {
        self.files.iter().find(|file| !file.status.is_success())
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
}
pub trait RouteProvider: Send + Sync {
    fn is_ignored(&self, path: &LayerPath) -> Result<bool, CommitError>;

    fn base_hash(&self, path: &LayerPath) -> Result<Option<String>, CommitError>;
}

pub struct CommitService<T: CommitTransactionPort + 'static> {
    commit_queue: CommitQueue<T>,
    route_provider: Arc<dyn RouteProvider>,
}

impl<T: CommitTransactionPort + 'static> CommitService<T> {
    pub fn with_route_provider(
        mut commit_queue: CommitQueue<T>,
        route_provider: Arc<dyn RouteProvider>,
    ) -> Result<Self, CommitError> {
        commit_queue.start()?;
        Ok(Self {
            commit_queue,
            route_provider,
        })
    }

    pub fn apply_changeset_with_base_hashes(
        &self,
        changes: &[LayerChange],
        snapshot_version: Option<u64>,
        atomic: bool,
        base_hashes: &[(LayerPath, Option<String>)],
    ) -> Result<ChangesetResult, CommitError> {
        let prepared = self.prepare_changeset_with_base_hashes(
            changes,
            snapshot_version,
            atomic,
            base_hashes,
        )?;
        self.apply_prepared_changeset(prepared)
    }

    pub fn prepare_changeset_with_base_hashes(
        &self,
        changes: &[LayerChange],
        snapshot_version: Option<u64>,
        atomic: bool,
        base_hashes: &[(LayerPath, Option<String>)],
    ) -> Result<PreparedChangeset, CommitError> {
        let mut path_groups = Vec::with_capacity(changes.len());
        let mut publishable = Vec::with_capacity(changes.len());
        for change in changes {
            let path = change.path().clone();
            if path.as_str() == ".git" || path.as_str().starts_with(".git/") {
                path_groups.push(PublishDecision {
                    path,
                    route: Route::Drop,
                    base_hash: None,
                    message: Some(".git paths are not mutable through OCC".to_owned()),
                });
                continue;
            }
            let route = if self.route_provider.is_ignored(&path)? {
                Route::Direct
            } else {
                Route::Gated
            };
            let base_hash = if route == Route::Gated {
                match base_hashes.iter().find(|(candidate, _)| candidate == &path) {
                    Some((_, hash)) => hash.clone(),
                    None => self.route_provider.base_hash(&path)?,
                }
            } else {
                None
            };
            path_groups.push(PublishDecision {
                path,
                route,
                base_hash,
                message: None,
            });
            publishable.push(change.clone());
        }
        Ok(PreparedChangeset {
            snapshot_version,
            path_groups,
            changes: publishable,
            atomic,
        })
    }

    fn apply_prepared_changeset(
        &self,
        prepared: PreparedChangeset,
    ) -> Result<ChangesetResult, CommitError> {
        let total_start = Instant::now();
        let snapshot_version = prepared.snapshot_version;
        let receiver = self.commit_queue.submit(prepared)?;
        let commit_start = Instant::now();
        let result = receiver
            .recv()
            .map_err(|_| CommitError::ReplyDisconnected)??;
        Ok(finalize_apply_result(
            result,
            snapshot_version,
            commit_start.elapsed().as_secs_f64(),
            total_start.elapsed().as_secs_f64(),
        ))
    }
}

fn finalize_apply_result(
    mut result: ChangesetResult,
    snapshot_version: Option<u64>,
    commit_elapsed_s: f64,
    total_s: f64,
) -> ChangesetResult {
    let commit_queue_wait_s = timing_or_default(&result.timings, "occ.serial.queue_wait_s");
    let commit_worker_s = timing_or_default(&result.timings, "occ.commit.total_s")
        .max(timing_or_default(&result.timings, "occ.serial.commit_s"));
    result.timings.insert(
        "occ.apply.commit_queue_wait_s".to_owned(),
        commit_queue_wait_s,
    );
    result
        .timings
        .insert("occ.apply.commit_resume_wait_s".to_owned(), 0.0);
    result
        .timings
        .insert("occ.apply.commit_worker_s".to_owned(), commit_worker_s);
    result
        .timings
        .insert("occ.apply.commit_s".to_owned(), commit_elapsed_s);
    result
        .timings
        .insert("occ.apply.total_s".to_owned(), total_s);
    if let (Some(published), Some(snapshot)) = (result.published_manifest_version, snapshot_version)
    {
        result.timings.insert(
            "occ.apply.manifest_lag".to_owned(),
            published.saturating_sub(snapshot + 1) as f64,
        );
    }
    result
}

fn timing_or_default(timings: &std::collections::BTreeMap<String, f64>, key: &str) -> f64 {
    timings.get(key).copied().unwrap_or(0.0)
}

impl<T: CommitTransactionPort + 'static> Drop for CommitService<T> {
    fn drop(&mut self) {
        let _ = self.commit_queue.close();
    }
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct RouteMetrics {
    pub gated_path_count: usize,
    pub direct_path_count: usize,
}

#[derive(Clone)]
pub struct StackRouteProvider {
    pub root: PathBuf,
}

impl RouteProvider for StackRouteProvider {
    fn is_ignored(&self, path: &LayerPath) -> std::result::Result<bool, CommitError> {
        // Per-call re-read of the active merged manifest: opening a fresh
        // `LayerStack` here is load-bearing, so a `.gitignore` edit committed
        // between ops is observed by the next route decision.
        let stack = LayerStack::open(self.root.clone())
            .map_err(|err| CommitError::RoutePreparation(err.to_string()))?;
        path_is_ignored(&stack, path.as_str())
            .map_err(|err| CommitError::RoutePreparation(err.to_string()))
    }

    fn base_hash(&self, path: &LayerPath) -> std::result::Result<Option<String>, CommitError> {
        let stack = LayerStack::open(self.root.clone())
            .map_err(|err| CommitError::RoutePreparation(err.to_string()))?;
        let (bytes, exists) = stack
            .read_bytes(path.as_str())
            .map_err(|err| CommitError::RoutePreparation(err.to_string()))?;
        Ok(hash_current(bytes.as_deref(), exists))
    }
}

pub fn route_metrics(
    root: &Path,
    changes: &[LayerChange],
) -> Result<RouteMetrics, LayerStackError> {
    let stack = LayerStack::open(root.to_path_buf())?;
    let mut metrics = RouteMetrics::default();
    for change in changes {
        let path = change.path().as_str();
        if path == ".git" || path.starts_with(".git/") {
            continue;
        }
        if path_is_ignored(&stack, path)? {
            metrics.direct_path_count += 1;
        } else {
            metrics.gated_path_count += 1;
        }
    }
    Ok(metrics)
}

pub fn insert_route_timings(
    timings: &mut serde_json::Map<String, Value>,
    metrics: RouteMetrics,
    route_s: f64,
    occ_s: f64,
) {
    for (key, value) in [
        ("occ.prepare.prepare_groups_s", route_s),
        ("occ.prepare.group_by_route_s", route_s),
        ("occ.prepare.route_and_base_hash_s", route_s),
        ("occ.prepare.total_s", route_s),
        ("occ.commit.total_s", occ_s),
        (
            "occ.commit.gated_path_count",
            usize_to_f64_saturating(metrics.gated_path_count),
        ),
        (
            "occ.commit.direct_path_count",
            usize_to_f64_saturating(metrics.direct_path_count),
        ),
    ] {
        timings.insert(key.to_owned(), json!(value));
    }
    for key in [
        "occ.commit.validate_groups_s",
        "occ.commit.publish_layer_s",
        "occ.commit.stager_write_total_s",
        "occ.commit.stager_write_count",
        "occ.commit.gated_read_current_total_s",
        "occ.commit.gated_apply_changes_total_s",
        "occ.commit.gated_stage_delta_total_s",
        "occ.commit.direct_read_current_total_s",
        "occ.commit.direct_apply_changes_total_s",
        "occ.commit.direct_stage_delta_total_s",
    ] {
        timings.entry(key.to_owned()).or_insert_with(|| json!(0.0));
    }
}

fn path_is_ignored(stack: &LayerStack, path: &str) -> Result<bool, LayerStackError> {
    let rel = path.trim_start_matches('/');
    if rel.is_empty() {
        return Ok(false);
    }
    // Directory-exclusion seal: if any ancestor directory of `path` is excluded
    // as a directory, `path` is ignored regardless of any deeper re-include.
    let parts: Vec<&str> = rel.split('/').collect();
    let mut accum = String::new();
    for part in &parts[..parts.len() - 1] {
        accum = join_rel(&accum, part);
        if dir_is_excluded(stack, &accum)? {
            return Ok(true);
        }
    }
    match_with_inheritance(stack, rel, false)
}

fn dir_is_excluded(stack: &LayerStack, dir_rel: &str) -> Result<bool, LayerStackError> {
    let mut accum = String::new();
    let mut excluded = false;
    for part in dir_rel.split('/').filter(|part| !part.is_empty()) {
        accum = join_rel(&accum, part);
        if !excluded {
            excluded = match_with_inheritance(stack, &accum, true)?;
        }
    }
    Ok(excluded)
}

fn match_with_inheritance(
    stack: &LayerStack,
    path: &str,
    as_dir: bool,
) -> Result<bool, LayerStackError> {
    let parts: Vec<&str> = path.split('/').collect();
    let mut ignored = false;
    let mut accum = String::new();
    for part in &parts {
        if let Some(matcher) = matcher_for(stack, &accum)? {
            // Pass `path` relative to `accum`. The matcher is rooted at `.`
            // (see `matcher_for`), so the crate performs no further stripping and
            // per-dir pattern anchoring (`/build`, `src/*.rs`) is preserved.
            let sub = if accum.is_empty() {
                path
            } else {
                path[accum.len()..].trim_start_matches('/')
            };
            if !sub.is_empty() {
                match matcher.matched(sub, as_dir) {
                    Match::Ignore(_) => ignored = true,
                    Match::Whitelist(_) => ignored = false,
                    Match::None => {}
                }
            }
        }
        accum = join_rel(&accum, part);
    }
    Ok(ignored)
}

fn matcher_for(
    stack: &LayerStack,
    dir_rel: &str,
) -> Result<Option<ignore::gitignore::Gitignore>, LayerStackError> {
    let rel = join_rel(dir_rel, ".gitignore");
    let (bytes, exists) = stack.read_bytes(&rel)?;
    if !exists {
        return Ok(None);
    }
    let Some(bytes) = bytes else {
        return Ok(None);
    };
    let Ok(text) = String::from_utf8(bytes) else {
        return Ok(None);
    };
    // Root `.` (not `dir_rel`): the caller in `match_with_inheritance` already
    // makes the candidate relative to this directory, and the `ignore` crate's
    // `Gitignore::matched` re-strips its root by raw byte prefix — rooting at
    // `dir_rel` would strip it a second time whenever a child component repeats
    // the directory name (e.g. `a/.gitignore` `/x` vs `a/a/x`). Root `.` disables
    // that strip; per-pattern anchoring comes from the pattern text, not the root.
    let mut builder = GitignoreBuilder::new(".");
    for line in text.lines() {
        // `add_line` skips comments/blanks itself; ignore malformed patterns.
        let _ = builder.add_line(None, line);
    }
    Ok(builder.build().ok())
}

fn join_rel(prefix: &str, child: &str) -> String {
    if prefix.is_empty() {
        child.to_owned()
    } else {
        format!("{prefix}/{child}")
    }
}
pub fn base_hashes_for_snapshot(
    root: &Path,
    manifest: &Manifest,
    changes: &[LayerChange],
) -> Result<Vec<(LayerPath, Option<String>)>, LayerStackError> {
    let view = MergedView::new(root.to_path_buf());
    changes
        .iter()
        .map(|change| {
            if matches!(change, LayerChange::OpaqueDir { .. }) {
                return Ok((change.path().clone(), None));
            }
            let (bytes, exists) = view.read_bytes(change.path().as_str(), manifest)?;
            Ok((
                change.path().clone(),
                hash_current(bytes.as_deref(), exists),
            ))
        })
        .collect()
}

#[must_use]
pub fn hash_current(content: Option<&[u8]>, exists: bool) -> Option<String> {
    if !exists {
        return None;
    }
    content.map(hash_bytes)
}

#[must_use]
pub fn hash_bytes(content: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(content);
    hex_lower(&hasher.finalize())
}

fn hex_lower(bytes: &[u8]) -> String {
    const LOWER_HEX: &[u8; 16] = b"0123456789abcdef";

    let mut out = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        out.push(char::from(LOWER_HEX[usize::from(byte >> 4)]));
        out.push(char::from(LOWER_HEX[usize::from(byte & 0x0f)]));
    }
    out
}

pub(crate) fn usize_to_f64_saturating(value: usize) -> f64 {
    u32::try_from(value).map_or_else(|_| f64::from(u32::MAX), f64::from)
}

pub(crate) fn i64_to_f64_saturating(value: i64) -> f64 {
    u64::try_from(value).map_or(0.0, |value| {
        u32::try_from(value).map_or_else(|_| f64::from(u32::MAX), f64::from)
    })
}


#[cfg(test)]
#[path = "../../tests/unit/commit/prepare.rs"]
mod prepare_tests;
#[cfg(test)]
#[path = "../../tests/unit/route.rs"]
mod route_tests;
