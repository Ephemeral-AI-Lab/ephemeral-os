use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use ignore::gitignore::GitignoreBuilder;
use ignore::Match;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::model::{hex_lower, CasError, LayerChange, LayerPath};
use crate::{LayerStack, LayerStackError, Manifest, MergedView};

mod worker;

use worker::{CommitQueue, CommitTransaction, PreparedChangeset};

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

    #[error("occ route preparation failed: {0}")]
    RoutePreparation(String),

    #[error(transparent)]
    Cas(#[from] CasError),

    #[error(transparent)]
    Storage(#[from] crate::LayerStackError),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Route {
    Gated,
    Direct,
    Drop,
}

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
pub(crate) struct PublishDecision {
    pub(crate) path: LayerPath,
    pub(crate) route: Route,
    pub(crate) base_hash: Option<String>,
    pub(crate) message: Option<String>,
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

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CaptureRouteStats {
    pub gated_path_count: usize,
    pub direct_path_count: usize,
    pub drop_path_count: usize,
    pub direct_bytes: u64,
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

pub(crate) struct CommitWriter {
    root: PathBuf,
    commit_queue: CommitQueue,
}

impl CommitWriter {
    pub(crate) fn with_options(root: PathBuf, options: CommitOptions) -> Result<Self, CommitError> {
        let options = CommitOptions::new(options.auto_squash_max_depth);
        let transaction = CommitTransaction {
            root: root.clone(),
            options,
        };
        let mut commit_queue = CommitQueue::new(transaction);
        commit_queue.start()?;
        Ok(Self { root, commit_queue })
    }

    pub(crate) fn apply_changeset_with_base_hashes(
        &self,
        changes: &[LayerChange],
        snapshot_version: Option<u64>,
        atomic: bool,
        base_hashes: &[(LayerPath, Option<String>)],
    ) -> Result<ChangesetResult, CommitError> {
        let stack = self.open_stack()?;
        let mut path_groups = Vec::with_capacity(changes.len());
        for change in changes {
            let path = change.path().clone();
            let route = route_for_path(&stack, &path)
                .map_err(|err| CommitError::RoutePreparation(err.to_string()))?;
            let base_hash = if route == Route::Gated {
                match base_hashes.iter().find(|(candidate, _)| candidate == &path) {
                    Some((_, hash)) => hash.clone(),
                    None => stack_base_hash(&stack, &path)?,
                }
            } else {
                None
            };
            path_groups.push(PublishDecision {
                path,
                route,
                base_hash,
                message: drop_message(route),
            });
        }
        self.apply_changeset_with_decisions(changes, snapshot_version, atomic, path_groups)
    }

    pub(crate) fn apply_changeset_with_decisions(
        &self,
        changes: &[LayerChange],
        snapshot_version: Option<u64>,
        atomic: bool,
        path_groups: Vec<PublishDecision>,
    ) -> Result<ChangesetResult, CommitError> {
        if changes.len() != path_groups.len() {
            return Err(CommitError::RoutePreparation(format!(
                "changeset decision count mismatch: {} changes, {} decisions",
                changes.len(),
                path_groups.len()
            )));
        }
        let publishable = changes
            .iter()
            .zip(path_groups.iter())
            .filter(|(_, group)| group.route != Route::Drop)
            .map(|(change, _)| change.clone())
            .collect::<Vec<_>>();
        let handoff_event = worker_handoff_event(&path_groups, publishable.len(), atomic);
        let receiver = self.commit_queue.submit(PreparedChangeset {
            path_groups,
            changes: publishable,
            atomic,
        })?;
        let mut result = receiver
            .recv()
            .map_err(|_| CommitError::ReplyDisconnected)??;
        result.events.insert(0, handoff_event);
        if let (Some(published), Some(snapshot)) =
            (result.published_manifest_version, snapshot_version)
        {
            result.timings.insert(
                "occ.apply.manifest_lag".to_owned(),
                published.saturating_sub(snapshot + 1) as f64,
            );
        }
        Ok(result)
    }

    fn open_stack(&self) -> Result<LayerStack, CommitError> {
        LayerStack::open(self.root.clone())
            .map_err(|err| CommitError::RoutePreparation(err.to_string()))
    }
}

impl Drop for CommitWriter {
    fn drop(&mut self) {
        let _ = self.commit_queue.close();
    }
}

fn worker_handoff_event(
    path_groups: &[PublishDecision],
    publishable_change_count: usize,
    atomic: bool,
) -> OccTraceEvent {
    OccTraceEvent::new(
        "occ",
        "worker_handoff",
        json!({
            "path_count": path_groups.len(),
            "publishable_change_count": publishable_change_count,
            "atomic": atomic,
            "gated_path_count": route_count(path_groups, Route::Gated),
            "direct_path_count": route_count(path_groups, Route::Direct),
            "drop_path_count": route_count(path_groups, Route::Drop),
        }),
    )
}

fn route_count(path_groups: &[PublishDecision], route: Route) -> usize {
    path_groups
        .iter()
        .filter(|group| group.route == route)
        .count()
}

pub fn capture_route_stats_for_manifest(
    root: &Path,
    manifest: &Manifest,
    changes: &[LayerChange],
) -> Result<CaptureRouteStats, CommitError> {
    let decisions = publish_decisions_for_manifest(root, manifest, changes)?;
    let mut stats = CaptureRouteStats::default();
    for (change, decision) in changes.iter().zip(decisions.iter()) {
        match decision.route {
            Route::Gated => stats.gated_path_count += 1,
            Route::Direct => {
                stats.direct_path_count += 1;
                if let LayerChange::Write { content, .. } = change {
                    stats.direct_bytes = stats
                        .direct_bytes
                        .saturating_add(u64::try_from(content.len()).unwrap_or(u64::MAX));
                }
            }
            Route::Drop => stats.drop_path_count += 1,
        }
    }
    Ok(stats)
}

pub(crate) fn publish_decisions_for_manifest(
    root: &Path,
    manifest: &Manifest,
    changes: &[LayerChange],
) -> Result<Vec<PublishDecision>, CommitError> {
    let view = MergedView::new(root.to_path_buf());
    let source = ManifestIgnoreSource {
        view: &view,
        manifest,
    };
    changes
        .iter()
        .map(|change| {
            let path = change.path().clone();
            let route = route_for_path_from_source(&source, &path)
                .map_err(|err| CommitError::RoutePreparation(err.to_string()))?;
            let base_hash = if route == Route::Gated {
                snapshot_base_hash(&view, manifest, change)?
            } else {
                None
            };
            Ok(PublishDecision {
                path,
                route,
                base_hash,
                message: drop_message(route),
            })
        })
        .collect()
}

fn route_for_path(stack: &LayerStack, path: &LayerPath) -> Result<Route, LayerStackError> {
    route_for_path_from_source(stack, path)
}

fn route_for_path_from_source(
    source: &impl IgnoreSource,
    path: &LayerPath,
) -> Result<Route, LayerStackError> {
    if path.as_str() == ".git" || path.as_str().starts_with(".git/") {
        return Ok(Route::Drop);
    }
    if path_is_ignored(source, path.as_str())? {
        Ok(Route::Direct)
    } else {
        Ok(Route::Gated)
    }
}

fn drop_message(route: Route) -> Option<String> {
    (route == Route::Drop).then(|| ".git paths are not mutable through OCC".to_owned())
}

fn stack_base_hash(stack: &LayerStack, path: &LayerPath) -> Result<Option<String>, CommitError> {
    let (bytes, exists) = stack
        .read_bytes(path.as_str())
        .map_err(|err| CommitError::RoutePreparation(err.to_string()))?;
    Ok(hash_current(bytes.as_deref(), exists))
}

fn snapshot_base_hash(
    view: &MergedView,
    manifest: &Manifest,
    change: &LayerChange,
) -> Result<Option<String>, CommitError> {
    if matches!(change, LayerChange::OpaqueDir { .. }) {
        return Ok(None);
    }
    let (bytes, exists) = view
        .read_bytes(change.path().as_str(), manifest)
        .map_err(|err| CommitError::RoutePreparation(err.to_string()))?;
    Ok(hash_current(bytes.as_deref(), exists))
}

trait IgnoreSource {
    fn read_bytes(&self, path: &str) -> Result<(Option<Vec<u8>>, bool), LayerStackError>;
}

impl IgnoreSource for LayerStack {
    fn read_bytes(&self, path: &str) -> Result<(Option<Vec<u8>>, bool), LayerStackError> {
        Self::read_bytes(self, path)
    }
}

struct ManifestIgnoreSource<'a> {
    view: &'a MergedView,
    manifest: &'a Manifest,
}

impl IgnoreSource for ManifestIgnoreSource<'_> {
    fn read_bytes(&self, path: &str) -> Result<(Option<Vec<u8>>, bool), LayerStackError> {
        self.view.read_bytes(path, self.manifest)
    }
}

fn path_is_ignored(source: &impl IgnoreSource, path: &str) -> Result<bool, LayerStackError> {
    let rel = path.trim_start_matches('/');
    if rel.is_empty() {
        return Ok(false);
    }
    let parts: Vec<&str> = rel.split('/').collect();
    let mut accum = String::new();
    for part in &parts[..parts.len() - 1] {
        accum = join_rel(&accum, part);
        if dir_is_excluded(source, &accum)? {
            return Ok(true);
        }
    }
    match_with_inheritance(source, rel, false)
}

fn dir_is_excluded(source: &impl IgnoreSource, dir_rel: &str) -> Result<bool, LayerStackError> {
    let mut accum = String::new();
    let mut excluded = false;
    for part in dir_rel.split('/').filter(|part| !part.is_empty()) {
        accum = join_rel(&accum, part);
        if !excluded {
            excluded = match_with_inheritance(source, &accum, true)?;
        }
    }
    Ok(excluded)
}

fn match_with_inheritance(
    source: &impl IgnoreSource,
    path: &str,
    as_dir: bool,
) -> Result<bool, LayerStackError> {
    let parts: Vec<&str> = path.split('/').collect();
    let mut ignored = false;
    let mut accum = String::new();
    for part in &parts {
        if let Some(matcher) = matcher_for(source, &accum)? {
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
    source: &impl IgnoreSource,
    dir_rel: &str,
) -> Result<Option<ignore::gitignore::Gitignore>, LayerStackError> {
    let rel = join_rel(dir_rel, ".gitignore");
    let (bytes, exists) = source.read_bytes(&rel)?;
    if !exists {
        return Ok(None);
    }
    let Some(bytes) = bytes else {
        return Ok(None);
    };
    let Ok(text) = String::from_utf8(bytes) else {
        return Ok(None);
    };
    let mut builder = GitignoreBuilder::new(".");
    for line in text.lines() {
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

#[cfg(test)]
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
    content.map(|content| {
        let mut hasher = Sha256::new();
        hasher.update(content);
        hex_lower(hasher.finalize())
    })
}

#[cfg(test)]
#[path = "../../tests/unit/route.rs"]
mod route_tests;
