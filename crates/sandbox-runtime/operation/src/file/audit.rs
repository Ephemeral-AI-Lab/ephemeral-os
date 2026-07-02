//! `record_layer_publish` (C3 spec §9/§13): after a layer commits, map each
//! resolved line's structural [`Origin`] to an owner string and append one audit
//! event per path. This is the only place owner strings are minted; `blame`
//! reads them back verbatim. Best-effort — a dropped event reconciles to
//! `unknown`.

use std::collections::HashMap;

use sandbox_runtime_layerstack::{LayerChange, LayerPath, LineRange, Origin};
use sha2::{Digest, Sha256};

use super::service::store::{AuditEvent, OwnerRange};
use crate::file::FileService;

const ORIGINAL_OWNER: &str = "original";
const UNKNOWN_OWNER: &str = "unknown";

impl FileService {
    /// Append one audit event per resolved path, **after** the layer commits.
    /// `Command` lines are this publish's `owner`; `Active(i)` lines inherit the
    /// owner the latest event recorded for active line `i` (absent → `original`).
    /// An empty range list is wholesale attribution (non-text / ignored).
    ///
    /// Never fails the publish: a dropped event reconciles to `unknown` on the
    /// next open (the merged bytes cannot reconstruct origin).
    pub fn record_layer_publish(
        &self,
        owner: &str,
        origin: &[(LayerPath, Vec<(LineRange, Origin)>)],
        changes: &[LayerChange],
    ) {
        for (path, ranges) in origin {
            let content_digest = content_digest(changes, path);
            let event = if ranges.is_empty() {
                AuditEvent {
                    path: path.as_str().to_owned(),
                    line_count: 1,
                    default_owner: owner.to_owned(),
                    owner_ranges: Vec::new(),
                    content_digest,
                }
            } else {
                command_only_event(path.as_str(), ranges, owner, content_digest.clone())
                    .unwrap_or_else(|| {
                        let latest = self.store().latest(path.as_str());
                        let line_owners = resolve_line_owners(ranges, owner, latest.as_ref());
                        event_from_line_owners(path.as_str(), &line_owners, content_digest)
                    })
            };
            let _ = self.store().append(&event);
        }
    }
}

/// Per-line owner for the committed content, in line order (index 0 == line 1).
fn resolve_line_owners(
    ranges: &[(LineRange, Origin)],
    owner: &str,
    latest: Option<&AuditEvent>,
) -> Vec<String> {
    let mut owners = Vec::new();
    for (range, origin) in ranges {
        for offset in 0..range.len {
            owners.push(match origin {
                Origin::Command => owner.to_owned(),
                Origin::Active(active_idx) => {
                    active_owner(latest, (*active_idx + offset) as u64 + 1)
                }
            });
        }
    }
    owners
}

/// Owner of active line `active_line` (1-based) from the latest event: no event
/// → `original`; a line the event does not cover → `unknown`.
fn active_owner(latest: Option<&AuditEvent>, active_line: u64) -> String {
    match latest {
        None => ORIGINAL_OWNER.to_owned(),
        Some(event) if active_line >= 1 && active_line <= event.line_count => {
            owner_at(event, active_line).to_owned()
        }
        Some(_) => UNKNOWN_OWNER.to_owned(),
    }
}

fn owner_at(event: &AuditEvent, line: u64) -> &str {
    for range in &event.owner_ranges {
        if line >= range.start_line && line < range.start_line + range.line_count {
            return &range.owner;
        }
    }
    &event.default_owner
}

fn command_only_event(
    path: &str,
    ranges: &[(LineRange, Origin)],
    owner: &str,
    content_digest: String,
) -> Option<AuditEvent> {
    let mut line_count = 0u64;
    for (range, origin) in ranges {
        if !matches!(origin, Origin::Command) {
            return None;
        }
        line_count += range.len as u64;
    }
    Some(AuditEvent {
        path: path.to_owned(),
        line_count,
        default_owner: owner.to_owned(),
        owner_ranges: Vec::new(),
        content_digest,
    })
}

/// Build an event from per-line owners: the most-covering owner becomes
/// `default_owner`, every other maximal run becomes a sparse `owner_range`.
fn event_from_line_owners(path: &str, owners: &[String], content_digest: String) -> AuditEvent {
    let default_owner = most_common_owner(owners)
        .unwrap_or(ORIGINAL_OWNER)
        .to_owned();
    let mut owner_ranges: Vec<OwnerRange> = Vec::new();
    for (index, owner) in owners.iter().enumerate() {
        if *owner == default_owner {
            continue;
        }
        let line = index as u64 + 1;
        if let Some(last) = owner_ranges.last_mut() {
            if last.owner == *owner && last.start_line + last.line_count == line {
                last.line_count += 1;
                continue;
            }
        }
        owner_ranges.push(OwnerRange {
            start_line: line,
            line_count: 1,
            owner: owner.clone(),
        });
    }
    AuditEvent {
        path: path.to_owned(),
        line_count: owners.len() as u64,
        default_owner,
        owner_ranges,
        content_digest,
    }
}

fn most_common_owner(owners: &[String]) -> Option<&str> {
    let mut best: Option<(&str, usize)> = None;
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for owner in owners {
        let count = counts.entry(owner.as_str()).or_insert(0);
        *count += 1;
        match best {
            Some((_, best_count)) if best_count >= *count => {}
            _ => best = Some((owner.as_str(), *count)),
        }
    }
    best.map(|(owner, _)| owner)
}

fn content_digest(changes: &[LayerChange], path: &LayerPath) -> String {
    let Some(change) = changes.iter().find(|change| change.path() == path) else {
        return String::new();
    };
    let mut hasher = Sha256::new();
    match change {
        LayerChange::Write { content, .. } => hasher.update(content),
        LayerChange::WriteFile { source_path, .. } => match std::fs::read(source_path) {
            Ok(bytes) => hasher.update(bytes),
            Err(_) => return String::new(),
        },
        LayerChange::Delete { .. }
        | LayerChange::Symlink { .. }
        | LayerChange::OpaqueDir { .. } => return String::new(),
    }
    format!("sha256:{:x}", hasher.finalize())
}
