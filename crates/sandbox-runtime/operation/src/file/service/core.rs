//! [`FileService`]: the read side (`blame`) and the post-commit write side
//! (`record_publish`) over the file-auditability store.
//!
//! `blame` is a **pure store read** — no mount, no layerstack read, no live
//! digest check (C3 spec §11.1). `record_publish` maps each resolved line's
//! structural [`Origin`] to an owner string and appends one event per path
//! after the layer commits; it is the only place owner strings are minted.

use std::path::PathBuf;

use sandbox_runtime_layerstack::{LayerChange, LayerPath, LineRange, Origin};
use sha2::{Digest, Sha256};

use super::store::{AuditEvent, FileAuditabilityStore, OwnerRange};
use crate::file::error::FileError;

const ORIGINAL_OWNER: &str = "original";
const UNKNOWN_OWNER: &str = "unknown";

pub struct FileService {
    store: FileAuditabilityStore,
}

/// One run of consecutive lines that share an owner. `owner` is opaque — the
/// `file` domain never parses `workspace_session:` / `operation:` / `original`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BlameRange {
    pub start_line: u64,
    pub line_count: u64,
    pub owner: String,
}

impl FileService {
    /// Open the store under `dir` (created if absent), rebuilding its in-memory
    /// index from the NDJSON segments.
    ///
    /// # Errors
    /// Returns an I/O error if the directory or its segments cannot be read.
    pub fn open(dir: PathBuf) -> std::io::Result<Self> {
        Ok(Self {
            store: FileAuditabilityStore::open(dir)?,
        })
    }

    /// Whole-file blame: the latest audit event for `path`, tiled over
    /// `[1..=line_count]` from `default_owner` + sparse `owner_ranges`, with
    /// equal-owner neighbours coalesced. `path` is normalized through the same
    /// [`LayerPath`] rules the audit key uses (`./src/x` == `src/x`).
    ///
    /// # Errors
    /// Returns [`FileError::NotFound`] when `path` is invalid or unaudited.
    pub fn blame(&self, path: &str) -> Result<Vec<BlameRange>, FileError> {
        let key =
            LayerPath::parse(path).map_err(|_| FileError::NotFound(path.to_owned()))?;
        let event = self
            .store
            .latest(key.as_str())
            .ok_or_else(|| FileError::NotFound(key.as_str().to_owned()))?;
        Ok(tile(&event))
    }

    /// Map each resolved path's structural origin to an owner string and append
    /// one audit event per path, **after** the layer has committed. `Command`
    /// lines are this publish's `owner`; `Active(i)` lines inherit the owner the
    /// latest event recorded for active line `i` (absent → `original`).
    ///
    /// Best-effort and never fails the publish: a dropped event reconciles to
    /// `unknown` on the next open (the merged bytes can't reconstruct origin).
    pub fn record_publish(
        &self,
        owner: &str,
        origin: &[(LayerPath, Vec<(LineRange, Origin)>)],
        changes: &[LayerChange],
    ) {
        for (path, ranges) in origin {
            let digest = content_digest(changes, path);
            let event = if ranges.is_empty() {
                AuditEvent {
                    path: path.as_str().to_owned(),
                    line_count: 1,
                    default_owner: owner.to_owned(),
                    owner_ranges: Vec::new(),
                    content_digest: digest,
                }
            } else {
                let latest = self.store.latest(path.as_str());
                let line_owners = resolve_line_owners(ranges, owner, latest.as_ref());
                event_from_line_owners(path.as_str(), &line_owners, digest)
            };
            let _ = self.store.append(&event);
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
                    let active_line = (*active_idx + offset) as u64 + 1;
                    active_owner(latest, active_line)
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

/// Build an event from per-line owners: the most-covering owner becomes
/// `default_owner`, every other maximal run becomes a sparse `owner_range`.
fn event_from_line_owners(path: &str, owners: &[String], content_digest: String) -> AuditEvent {
    let default_owner = most_common_owner(owners).unwrap_or(ORIGINAL_OWNER).to_owned();
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
    for owner in owners {
        let count = owners.iter().filter(|other| *other == owner).count();
        match best {
            Some((_, best_count)) if best_count >= count => {}
            _ => best = Some((owner.as_str(), count)),
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
        LayerChange::Delete { .. } | LayerChange::Symlink { .. } | LayerChange::OpaqueDir { .. } => {
            return String::new()
        }
    }
    format!("sha256:{:x}", hasher.finalize())
}

fn tile(event: &AuditEvent) -> Vec<BlameRange> {
    if event.line_count == 0 {
        return Vec::new();
    }
    let mut owners: Vec<&str> = vec![event.default_owner.as_str(); event.line_count as usize];
    for range in &event.owner_ranges {
        let start = range.start_line.saturating_sub(1) as usize;
        for offset in 0..range.line_count as usize {
            if let Some(slot) = owners.get_mut(start + offset) {
                *slot = range.owner.as_str();
            }
        }
    }
    let mut ranges: Vec<BlameRange> = Vec::new();
    for (index, owner) in owners.iter().enumerate() {
        let line = index as u64 + 1;
        if let Some(last) = ranges.last_mut() {
            if last.owner == *owner {
                last.line_count += 1;
                continue;
            }
        }
        ranges.push(BlameRange {
            start_line: line,
            line_count: 1,
            owner: (*owner).to_owned(),
        });
    }
    ranges
}
