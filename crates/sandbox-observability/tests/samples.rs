use std::error::Error;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use sandbox_observability::{SampleReader, SampleSink};
use serde_json::json;

type TestResult = Result<(), Box<dyn Error>>;

static NEXT: AtomicU64 = AtomicU64::new(0);

fn temp_log(label: &str) -> PathBuf {
    std::env::temp_dir()
        .join(format!(
            "sandbox-obs-samples-{label}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ))
        .join("samples.ndjson")
}

#[test]
fn sink_appends_and_reader_windows_by_timestamp() -> TestResult {
    let path = temp_log("window");
    let sink = SampleSink::new(path.clone());
    sink.append(&json!({ "ts": 100, "scope": "stack", "layer_count": 1 }))?;
    sink.append(&json!({ "ts": 200, "scope": "stack", "layer_count": 2 }))?;
    sink.append(&json!({ "ts": 300, "scope": "stack", "layer_count": 3 }))?;

    let reader = SampleReader::new(path.clone());
    assert_eq!(reader.samples(0).len(), 3);
    let recent = reader.samples(200);
    assert_eq!(recent.len(), 2);
    assert_eq!(recent[0]["ts"], json!(200));
    assert_eq!(recent[1]["layer_count"], json!(3));

    let _ = fs::remove_dir_all(path.parent().expect("parent"));
    Ok(())
}

#[test]
fn reader_skips_malformed_lines_and_missing_log() -> TestResult {
    let path = temp_log("malformed");

    // Missing log: empty, no panic.
    assert!(SampleReader::new(path.clone()).samples(0).is_empty());

    let sink = SampleSink::new(path.clone());
    sink.append(&json!({ "ts": 10, "scope": "stack" }))?;
    // A half-written tail line must be skipped, not panic.
    fs::write(&path, "{\"ts\":10,\"scope\":\"stack\"}\n{\"ts\":20,\"sco")?;

    let samples = SampleReader::new(path.clone()).samples(0);
    assert_eq!(samples.len(), 1);
    assert_eq!(samples[0]["ts"], json!(10));

    let _ = fs::remove_dir_all(path.parent().expect("parent"));
    Ok(())
}
