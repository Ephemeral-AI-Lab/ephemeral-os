//! `blame` (C3 spec §11/§11.1): a **pure store read** — no mount, no layerstack
//! read, no live digest check. The latest audit event for a path is tiled over
//! `[1..=line_count]` from `default_owner` + sparse `owner_ranges`, coalescing
//! equal-owner neighbours.

use sandbox_runtime_layerstack::LayerPath;

use super::super::store::AuditEvent;
use crate::file::{BlameRange, FileError, FileService};

impl FileService {
    /// Whole-file blame. `path` is normalized through the same [`LayerPath`]
    /// rules the audit key uses (`./src/x` == `src/x`).
    ///
    /// # Errors
    /// Returns [`FileError::NotFound`] when `path` is invalid or unaudited.
    pub fn blame(&self, path: &str) -> Result<Vec<BlameRange>, FileError> {
        let key = LayerPath::parse(path).map_err(|_| FileError::NotFound(path.to_owned()))?;
        let event = self
            .store()
            .latest(key.as_str())
            .ok_or_else(|| FileError::NotFound(key.as_str().to_owned()))?;
        Ok(tile(&event))
    }
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
