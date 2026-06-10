use std::path::{Path, PathBuf};

use ignore::gitignore::GitignoreBuilder;
use ignore::Match;
use serde_json::{json, Value};

use crate::model::{LayerChange, LayerPath};

use crate::commit::error::CommitError;
use crate::commit::prepare::RouteProvider;
use crate::commit::{hash_current, usize_to_f64_saturating};
use crate::{LayerStack, LayerStackError};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct RouteMetrics {
    pub gated_path_count: usize,
    pub direct_path_count: usize,
}

/// [`RouteProvider`] impl that resolves DIRECT-vs-GATED routing and
/// base hashes from the active merged manifest of `root`.
#[derive(Clone)]
pub struct StackRouteProvider {
    /// The layer-stack root whose merged manifest is consulted per call.
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

/// Telemetry-only DIRECT/GATED tally over `changes`, sharing the route decision
/// with [`StackRouteProvider::is_ignored`].
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

/// This is the one shared routine behind both `StackRouteProvider::is_ignored`
/// (DIRECT vs GATED) and `route_metrics` (telemetry). It preserves the
/// daemon's gitignore contract: per-directory `.gitignore` read from the merged
/// snapshot, deeper-wins inheritance, and the directory-exclusion seal (an
/// excluded ancestor dir seals its whole subtree; a deeper `!` re-include cannot
/// rescue it).
///
/// All `.gitignore` reads go through `stack.read_bytes`, i.e. the active merged
/// manifest (newest-layer-wins, whiteout-aware) — the same view the overlay mount
/// projects, never a disk-walk. The per-pattern matching (dir-only-at-any-depth,
/// `*`-not-crossing-`/`, `**`, `!` ordering, char classes) is delegated to the
/// `ignore` crate's gitignore engine.
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

/// Is directory `dir_rel` excluded? Walks its components root→leaf; once an
/// ancestor is excluded the whole chain stays excluded (Git's directory seal).
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

/// Last-match-wins evaluation across every `.gitignore` at or above `path`'s
/// ancestor directories (root → `path`'s parent), deeper directories overriding
/// shallower ones. The caller owns the directory seal; this is the unsealed
/// evaluator. `as_dir` lets directory-only patterns (`foo/`) fire.
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

/// Build the gitignore matcher for `dir_rel`'s own `.gitignore`, read from the
/// merged snapshot. A missing, non-UTF-8, or unparseable file contributes no
/// patterns (`Ok(None)`) — the safe, validated GATED route. Only a genuine
/// `read_bytes` I/O error propagates.
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

/// Join a relative dir prefix with a child component (`""` + `c` -> `c`).
fn join_rel(prefix: &str, child: &str) -> String {
    if prefix.is_empty() {
        child.to_owned()
    } else {
        format!("{prefix}/{child}")
    }
}

#[cfg(test)]
mod tests;
