//! Minimal newline-delimited JSON sample log: an append-only `SampleSink` and a
//! windowed `SampleReader`. This is the skeleton the periodic stack sample needs;
//! the full span/trace log builds on the same format.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;

use serde_json::Value;

/// Append-only NDJSON sink for observability sample records.
pub struct SampleSink {
    path: PathBuf,
}

impl SampleSink {
    #[must_use]
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }

    /// Append one record as a single newline-delimited JSON line, creating the
    /// parent directory on demand.
    pub fn append(&self, record: &Value) -> std::io::Result<()> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut line = serde_json::to_vec(record)?;
        line.push(b'\n');
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        file.write_all(&line)
    }
}

/// Reader that folds the NDJSON sample log into the records within a window.
pub struct SampleReader {
    path: PathBuf,
}

impl SampleReader {
    #[must_use]
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }

    /// Records whose `ts` is at or after `since_unix_ms`, in append order.
    /// Malformed lines are skipped and a missing log yields an empty list, so a
    /// half-written tail never panics.
    #[must_use]
    pub fn samples(&self, since_unix_ms: i64) -> Vec<Value> {
        let Ok(contents) = fs::read_to_string(&self.path) else {
            return Vec::new();
        };
        contents
            .lines()
            .filter_map(|line| serde_json::from_str::<Value>(line).ok())
            .filter(|record| {
                record
                    .get("ts")
                    .and_then(Value::as_i64)
                    .is_some_and(|ts| ts >= since_unix_ms)
            })
            .collect()
    }
}
