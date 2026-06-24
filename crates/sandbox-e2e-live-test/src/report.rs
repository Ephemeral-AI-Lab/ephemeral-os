use std::fs;
use std::path::Path;

use serde_json::json;

use crate::cli_client::CallRecord;

const EXCHANGE_SCHEMA_VERSION: u32 = 1;

/// Write `{run_root}/reports/{sandbox_id}/exchange.jsonl`: a `{schema_version}`
/// header line followed by one JSON object per call record. Creates the report
/// dir. Best-effort: returns `io::Result` so the caller (`Sandbox::drop`) can
/// swallow failures without aborting teardown.
pub fn write_exchange(
    run_root: &Path,
    sandbox_id: &str,
    records: &[CallRecord],
) -> std::io::Result<()> {
    let report_dir = run_root.join("reports").join(sandbox_id);
    fs::create_dir_all(&report_dir)?;

    let mut body = json!({ "schema_version": EXCHANGE_SCHEMA_VERSION }).to_string();
    body.push('\n');
    for record in records {
        body.push_str(&record.to_exchange_line().to_string());
        body.push('\n');
    }

    fs::write(report_dir.join("exchange.jsonl"), body)
}
