//! Append-only NDJSON sink: one full line per `Record`, written with a single
//! `write_all` to an `O_APPEND` fd. On the in-container fs the kernel serializes
//! per-inode appends, so daemon (`d-*`) and namespace-process (`np-*`) lines
//! never interleave at any length.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;

use serde_json::Value;

use crate::record::{Attrs, Event, Record, Sample, Span, MAX_LINE_BYTES};

/// Append-only sink over one log file. The parent directory is created once at
/// construction; each append opens an `O_APPEND` fd, robust to the daemon
/// renaming the file at rotation.
pub struct Sink {
    path: PathBuf,
}

impl Sink {
    /// Open a sink over `path`, creating its parent directory once. Directory
    /// creation is best-effort — a real failure surfaces (and is swallowed) at
    /// the first `append`.
    #[must_use]
    pub fn new(path: PathBuf) -> Self {
        if let Some(parent) = path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        Self { path }
    }

    /// Append one record as a single newline-delimited line. The whole line is
    /// serialized once; if it exceeds `MAX_LINE_BYTES`, `attrs`/`metrics` are
    /// replaced with a `{"_truncated": <original_line_bytes>}` marker and the
    /// record is re-serialized exactly once — entries are never dropped
    /// piecemeal and the loop never repeats.
    pub fn append(&self, record: &Record) -> std::io::Result<()> {
        let mut line = serde_json::to_vec(record)?;
        if line.len() > MAX_LINE_BYTES {
            let original_len = line.len();
            line = serde_json::to_vec(&truncate(record, original_len))?;
        }
        line.push(b'\n');
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        file.write_all(&line)
    }
}

/// Replace only the open `attrs`/`metrics` bag with the truncation marker. For a
/// `Span`/`Event` the marker lands at `attrs._truncated` (nested); for a flattened
/// `Sample` it lands at the line's top level (`_truncated`) — a documented shape
/// asymmetry that preserves the layerstack slice's on-disk bytes.
fn truncate(record: &Record, original_len: usize) -> Record {
    match record {
        Record::Span(span) => Record::Span(Span {
            attrs: marker(original_len),
            ..span.clone()
        }),
        Record::Event(event) => Record::Event(Event {
            attrs: marker(original_len),
            ..event.clone()
        }),
        Record::Sample(sample) => Record::Sample(Sample {
            metrics: marker(original_len),
            ..sample.clone()
        }),
    }
}

fn marker(original_len: usize) -> Attrs {
    let mut map = Attrs::new();
    map.insert("_truncated".to_owned(), Value::from(original_len));
    map
}
