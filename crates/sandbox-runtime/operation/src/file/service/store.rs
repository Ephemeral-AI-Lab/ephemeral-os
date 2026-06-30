//! The file-auditability store (C3 spec §7/§7.1/§10): an append-only NDJSON log
//! loaded into an in-memory `path -> latest event` index on open. No database —
//! the records are tiny and the runtime is single-writer and ephemeral.
//!
//! `AuditEvent`/`OwnerRange` (de)serialize via `json!` + `serde_json::Value`
//! (no serde derive), matching `command_operations` and layerstack `model.rs`.

use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, PoisonError};

use serde_json::{json, Value};

const SEGMENT_PREFIX: &str = "file_auditability_";
const SEGMENT_SUFFIX: &str = ".ndjson";
const ACTIVE_SEGMENT: &str = "file_auditability_0.ndjson";

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct OwnerRange {
    pub(crate) start_line: u64,
    pub(crate) line_count: u64,
    pub(crate) owner: String,
}

/// One published path's line-ownership snapshot. The latest event per path is
/// the current blame; `default_owner` plus sparse `owner_ranges` keep it
/// `O(delta)`, and `content_digest` ties it to the bytes for reconcile only
/// (blame never reads it).
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AuditEvent {
    pub(crate) path: String,
    pub(crate) line_count: u64,
    pub(crate) default_owner: String,
    pub(crate) owner_ranges: Vec<OwnerRange>,
    pub(crate) content_digest: String,
}

impl AuditEvent {
    fn to_json(&self) -> Value {
        json!({
            "path": self.path,
            "line_count": self.line_count,
            "default_owner": self.default_owner,
            "owner_ranges": self
                .owner_ranges
                .iter()
                .map(|range| {
                    json!({
                        "start_line": range.start_line,
                        "line_count": range.line_count,
                        "owner": range.owner,
                    })
                })
                .collect::<Vec<_>>(),
            "content_digest": self.content_digest,
        })
    }

    fn from_json(value: &Value) -> Option<Self> {
        let owner_ranges = value
            .get("owner_ranges")
            .and_then(Value::as_array)
            .map(|ranges| {
                ranges
                    .iter()
                    .filter_map(|range| {
                        Some(OwnerRange {
                            start_line: range.get("start_line")?.as_u64()?,
                            line_count: range.get("line_count")?.as_u64()?,
                            owner: range.get("owner")?.as_str()?.to_owned(),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        Some(Self {
            path: value.get("path")?.as_str()?.to_owned(),
            line_count: value.get("line_count")?.as_u64()?,
            default_owner: value.get("default_owner")?.as_str()?.to_owned(),
            owner_ranges,
            content_digest: value
                .get("content_digest")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
        })
    }
}

pub(crate) struct FileAuditabilityStore {
    dir: PathBuf,
    index: Mutex<HashMap<String, AuditEvent>>,
}

impl FileAuditabilityStore {
    /// Open (creating the directory if absent) and rebuild the in-memory index
    /// by replaying every NDJSON segment in `<seq>` order; the latest line per
    /// path wins.
    pub(crate) fn open(dir: PathBuf) -> io::Result<Self> {
        std::fs::create_dir_all(&dir)?;
        let mut index = HashMap::new();
        for segment in segments(&dir)? {
            let file = File::open(&segment)?;
            for line in BufReader::new(file).lines() {
                let line = line?;
                if line.trim().is_empty() {
                    continue;
                }
                if let Some(event) =
                    serde_json::from_str::<Value>(&line).ok().and_then(|value| AuditEvent::from_json(&value))
                {
                    index.insert(event.path.clone(), event);
                }
            }
        }
        Ok(Self {
            dir,
            index: Mutex::new(index),
        })
    }

    /// Append one event after the layer commits: `write + fsync`, then update the
    /// index so blame on the same `FileService` sees it without a re-open.
    pub(crate) fn append(&self, event: &AuditEvent) -> io::Result<()> {
        let mut line = serde_json::to_string(&event.to_json()).map_err(io::Error::other)?;
        line.push('\n');
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.dir.join(ACTIVE_SEGMENT))?;
        file.write_all(line.as_bytes())?;
        file.sync_all()?;
        self.index
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .insert(event.path.clone(), event.clone());
        Ok(())
    }

    pub(crate) fn latest(&self, path: &str) -> Option<AuditEvent> {
        self.index
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .get(path)
            .cloned()
    }
}

fn segments(dir: &Path) -> io::Result<Vec<PathBuf>> {
    let mut segments = Vec::new();
    for entry in std::fs::read_dir(dir)? {
        let entry = entry?;
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        if name.starts_with(SEGMENT_PREFIX) && name.ends_with(SEGMENT_SUFFIX) {
            segments.push(entry.path());
        }
    }
    segments.sort();
    Ok(segments)
}
