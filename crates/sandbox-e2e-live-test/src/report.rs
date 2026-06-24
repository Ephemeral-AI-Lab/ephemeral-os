use std::fs;
use std::io;
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::cleanup::CleanupReport;
use crate::cli_client::CallRecord;
use crate::config::RunConfig;

const EXCHANGE_SCHEMA_VERSION: u32 = 1;
/// Load-bearing: the live `ManifestConfig` reader bails unless this is `1`.
const MANIFEST_SCHEMA_VERSION: u32 = 1;
pub const RESULT_SCHEMA_VERSION: u32 = 1;
pub const SUMMARY_SCHEMA_VERSION: u32 = 1;
/// Schema version stamped into every `observability.json` and the
/// `summary.observability` roll-up (§4).
pub const OBSERVABILITY_SCHEMA_VERSION: u32 = 1;

/// Per-snapshot cap on projected `recent_traces`; extra rows are dropped and a
/// warning is recorded (§4.1).
const RECENT_TRACE_CAP: usize = 50;
/// Per-snapshot cap on projected `resources.history`; same treatment (§4.1).
const RESOURCE_HISTORY_CAP: usize = 50;

const RUN_MANIFEST_FILE: &str = "run-manifest.json";
const SUMMARY_FILE: &str = "summary.json";

/// Write `{run_root}/reports/{sandbox_id}/exchange.jsonl`: a `{schema_version}`
/// header line followed by one JSON object per call record. Creates the report
/// dir. Best-effort: returns `io::Result` so the caller (`Sandbox::drop`) can
/// swallow failures without aborting teardown.
pub fn write_exchange(run_root: &Path, sandbox_id: &str, records: &[CallRecord]) -> io::Result<()> {
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

/// `{ total, failed }` assertion tally, shared by `result.json` and the summary
/// `tests[]` rollup.
#[derive(Serialize, Deserialize)]
pub struct Assertions {
    pub total: u64,
    pub failed: u64,
}

/// The per-sandbox `result.json` payload, written in `Sandbox::drop` (§5.2) and
/// re-read by the orchestrator when building the summary rollup (§5.3).
#[derive(Serialize, Deserialize)]
pub struct TestOutcome {
    pub schema_version: u32,
    pub test_name: String,
    pub sandbox_id: String,
    pub status: String,
    pub duration_ms: u128,
    pub workspace_root: String,
    pub assertions: Assertions,
    pub failure: Option<String>,
}

/// Write `{run_root}/reports/{sandbox_id}/result.json` into the same report dir
/// `write_exchange` creates. Best-effort, mirroring `write_exchange`.
pub fn write_result(run_root: &Path, outcome: &TestOutcome) -> io::Result<()> {
    let report_dir = run_root.join("reports").join(&outcome.sandbox_id);
    fs::create_dir_all(&report_dir)?;
    write_json_pretty(&report_dir.join("result.json"), outcome)
}

#[derive(Serialize)]
struct ManifestConfigSummary {
    max_parallel: usize,
    cleanup: &'static str,
    cli_timeout_secs: f64,
    build: String,
}

#[derive(Serialize)]
struct RunManifestDoc<'a> {
    schema_version: u32,
    gateway_socket: &'a Path,
    run_id: &'a str,
    image: &'a str,
    git_head: &'a str,
    config: ManifestConfigSummary,
    clock: &'a str,
}

/// Write the orchestrator-emitted `run-manifest.json` superset (§5.1). The four
/// load-bearing fields (`schema_version == 1`, `gateway_socket`, `run_id`,
/// `image`) stay readable by the live `ManifestConfig`; `git_head`/`config`/
/// `clock` are superset-only and ignored by that reader.
pub fn write_run_manifest(run_root: &Path, config: &RunConfig, git_head: &str) -> io::Result<()> {
    fs::create_dir_all(run_root)?;
    let doc = RunManifestDoc {
        schema_version: MANIFEST_SCHEMA_VERSION,
        gateway_socket: &config.gateway_socket,
        run_id: &config.run_id,
        image: &config.image,
        git_head,
        config: ManifestConfigSummary {
            max_parallel: config.max_parallel,
            cleanup: config.cleanup.as_str(),
            cli_timeout_secs: config.cli_timeout.as_secs_f64(),
            build: config.build.summary(),
        },
        clock: &config.clock,
    };
    write_json_pretty(&run_root.join(RUN_MANIFEST_FILE), &doc)
}

/// One `summary.tests[]` entry (§5.3), built from a `result.json` or synthesized
/// as `errored` for a report dir whose `result.json` is missing.
#[derive(Serialize)]
pub struct TestEntry {
    pub name: String,
    pub sandbox_id: String,
    pub status: String,
    pub duration_ms: u128,
    pub workspace_root: String,
    pub report_dir: String,
    pub assertions: Assertions,
    pub failure: Option<String>,
}

/// `summary.counts` rollup; `skipped` is always `0` under the orchestrator (§6).
#[derive(Serialize)]
pub struct Counts {
    pub total: usize,
    pub passed: usize,
    pub failed: usize,
    pub skipped: usize,
    pub errored: usize,
}

impl Counts {
    #[must_use]
    pub fn tally(tests: &[TestEntry]) -> Counts {
        let mut counts = Counts {
            total: tests.len(),
            passed: 0,
            failed: 0,
            skipped: 0,
            errored: 0,
        };
        for test in tests {
            match test.status.as_str() {
                "passed" => counts.passed += 1,
                "failed" => counts.failed += 1,
                "errored" => counts.errored += 1,
                _ => {}
            }
        }
        counts
    }
}

#[derive(Serialize)]
pub struct BuildTiming {
    pub gateway_build_ms: u128,
    pub cli_build_ms: u128,
    pub cargo_profile: String,
    pub cache_hit: bool,
}

#[derive(Serialize)]
pub struct RunnerTiming {
    pub wall_ms: u128,
    pub gateway_attach_ms: u128,
    pub test_process_ms: u128,
    pub teardown_ms: u128,
    pub max_parallel: usize,
}

#[derive(Serialize)]
pub struct PerTest {
    pub name: String,
    pub sandbox_id: String,
    pub total_ms: u128,
}

#[derive(Serialize)]
pub struct Timing {
    pub build: BuildTiming,
    pub runner: RunnerTiming,
    pub per_test: Vec<PerTest>,
}

/// The `summary.json` rollup (§5.3). Built solely from globbed `result.json`
/// plus the cargo-test exit code — never from libtest stdout.
#[derive(Serialize)]
pub struct Summary {
    pub schema_version: u32,
    pub run_id: String,
    pub git_head: String,
    pub started_at: String,
    pub finished_at: String,
    pub max_parallel: usize,
    pub status: String,
    pub counts: Counts,
    pub tests: Vec<TestEntry>,
    pub failed_tests: Vec<String>,
    pub artifacts_root: String,
    pub timing: Timing,
    pub cleanup: CleanupReport,
    pub observability: ObservabilitySummary,
}

/// Write `{run_root}/summary.json`.
pub fn write_summary(run_root: &Path, summary: &Summary) -> io::Result<()> {
    fs::create_dir_all(run_root)?;
    write_json_pretty(&run_root.join(SUMMARY_FILE), summary)
}

/// An `errored` `tests[]` entry keyed on the report dir name, used when a
/// `result.json` is absent or unreadable — no test identity is recoverable.
fn errored_entry(id: String, report_dir: String, failure: String) -> TestEntry {
    TestEntry {
        name: id.clone(),
        sandbox_id: id,
        status: "errored".to_owned(),
        duration_ms: 0,
        workspace_root: String::new(),
        report_dir,
        assertions: Assertions {
            total: 0,
            failed: 0,
        },
        failure: Some(failure),
    }
}

/// Build `summary.tests[]` by globbing `{run_root}/reports/*/`. A dir whose
/// `result.json` parses yields its recorded entry; a missing `result.json`
/// yields an `errored` entry (`"result.json missing"`) and an unparsable one an
/// `errored` entry naming the parse error (§5.3).
#[must_use]
pub fn build_tests(run_root: &Path) -> Vec<TestEntry> {
    let reports = run_root.join("reports");
    let Ok(read_dir) = fs::read_dir(&reports) else {
        return Vec::new();
    };
    let mut dirs: Vec<_> = read_dir
        .flatten()
        .filter(|entry| entry.path().is_dir())
        .collect();
    dirs.sort_by_key(std::fs::DirEntry::file_name);

    let mut tests = Vec::with_capacity(dirs.len());
    for entry in dirs {
        let id = entry.file_name().to_string_lossy().into_owned();
        let report_dir = entry.path();
        let report_dir_str = report_dir.to_string_lossy().into_owned();
        let test = match fs::read(report_dir.join("result.json")) {
            Ok(bytes) => match serde_json::from_slice::<TestOutcome>(&bytes) {
                Ok(outcome) => TestEntry {
                    name: outcome.test_name,
                    sandbox_id: outcome.sandbox_id,
                    status: outcome.status,
                    duration_ms: outcome.duration_ms,
                    workspace_root: outcome.workspace_root,
                    report_dir: report_dir_str,
                    assertions: outcome.assertions,
                    failure: outcome.failure,
                },
                Err(error) => errored_entry(
                    id,
                    report_dir_str,
                    format!("result.json unparsable: {error}"),
                ),
            },
            Err(_) => errored_entry(id, report_dir_str, "result.json missing".to_owned()),
        };
        tests.push(test);
    }
    tests
}

/// Latest-only `observability.json` artifact (§4): one bounded projection of the
/// public `get_observability_tree` node for a sandbox, its P1 cgroup verdict, and
/// the warnings the projection observed. Every degraded shape is a warning, never
/// a failure.
#[derive(Serialize)]
pub struct ObservabilitySnapshot {
    pub schema_version: u32,
    pub sandbox_id: String,
    pub captured_at: String,
    pub source_call: ObsSourceCall,
    pub poll_meta: ObsPollMeta,
    pub node: ObsNode,
    pub p1: P1,
    pub warnings: Vec<String>,
}

/// Metadata of the `get_observability_tree` call that produced a snapshot.
#[derive(Serialize)]
pub struct ObsSourceCall {
    pub argv: Vec<String>,
    pub exit_code: i32,
    pub latency_ms: u128,
}

/// How many poll cycles observed a sandbox id and which cycle wrote the latest snapshot.
#[derive(Serialize)]
pub struct ObsPollMeta {
    pub cycles_observed: u64,
    pub last_cycle_index: u64,
}

/// Bounded projection of one public observability-tree node. Drops the full
/// `workspaces[]`/`daemon` bodies, keeping only `workspace_count` and the
/// trace/resource summaries (§4.1).
#[derive(Serialize)]
pub struct ObsNode {
    pub lifecycle_state: Option<String>,
    pub availability: Option<String>,
    pub sampled_at_unix_ms: Option<i64>,
    pub errors: Vec<Value>,
    pub resources: ObsResources,
    pub recent_traces: Vec<ObsRecentTrace>,
    pub workspace_count: usize,
}

/// Latest plus bounded history of projected resource samples (§4.2).
#[derive(Serialize)]
pub struct ObsResources {
    pub latest: Option<ObsResourceSample>,
    pub history: Vec<ObsResourceSample>,
}

/// One projected resource sample: its timestamp, the verbatim `cgroup` object
/// (the P1 carrier), and an opaque `disk` pass-through (§4.2).
#[derive(Serialize)]
pub struct ObsResourceSample {
    pub sampled_at_unix_ms: Option<i64>,
    pub cgroup: Value,
    pub disk: Value,
}

/// Projected recent-trace summary: operation, status, and the duration signal
/// (§4.3). Identity/timestamp/message fields are dropped.
#[derive(Serialize)]
pub struct ObsRecentTrace {
    pub trace_id: Option<String>,
    pub kind: Option<String>,
    pub operation: Option<String>,
    pub status: Option<String>,
    pub duration_ms: Option<i64>,
    pub error_kind: Option<String>,
}

/// P1 cgroup detection over `node.resources.latest` (§5). Absence lowers
/// resolution and is recorded in `reason`, never raised.
#[derive(Serialize)]
pub struct P1 {
    pub available: bool,
    pub cpu_usage_usec: Option<i64>,
    pub memory_current_bytes: Option<i64>,
    pub memory_max_bytes: Option<i64>,
    pub memory_max_unlimited: Option<bool>,
    pub reason: Option<String>,
}

/// Non-gating `summary.observability` roll-up of the whole poll run (§4.4).
#[derive(Serialize)]
pub struct ObservabilitySummary {
    pub schema_version: u32,
    pub poll_cycles: u64,
    pub poll_errors: u64,
    pub snapshots_written: usize,
    pub p1_available: bool,
    pub warnings: Vec<String>,
}

/// Write `{run_root}/reports/{sandbox_id}/observability.json` (latest-only),
/// creating the report dir like `write_exchange`/`write_result`. Best-effort:
/// the returned `io::Result` lets the poller record a write failure as a warning
/// rather than fail the run.
pub fn write_observability(run_root: &Path, snapshot: &ObservabilitySnapshot) -> io::Result<()> {
    let report_dir = run_root.join("reports").join(&snapshot.sandbox_id);
    fs::create_dir_all(&report_dir)?;
    write_json_pretty(&report_dir.join("observability.json"), snapshot)
}

/// Project one `sandboxes[i]` node into its bounded [`ObsNode`], [`P1`] block,
/// and the warnings observed (§4.2/§4.3/§5). Pure over `(sandbox_id, node)`; the
/// caller keys the snapshot and adds source-call/poll metadata.
#[must_use]
pub fn observability_node_from_tree(sandbox_id: &str, node: &Value) -> (ObsNode, P1, Vec<String>) {
    let mut warnings = Vec::new();

    let availability = node
        .get("availability")
        .and_then(Value::as_str)
        .map(str::to_owned);
    if availability.as_deref() == Some("unavailable") {
        warnings.push(format!("node unavailable for {sandbox_id}"));
    }

    let errors = node
        .get("errors")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let resources = project_resources(sandbox_id, node.get("resources"), &mut warnings);
    let recent_traces = project_recent_traces(sandbox_id, node.get("recent_traces"), &mut warnings);
    let workspace_count = node
        .get("workspaces")
        .and_then(Value::as_array)
        .map_or(0, Vec::len);

    let (p1, p1_warning) = project_p1(sandbox_id, resources.latest.as_ref());
    if let Some(warning) = p1_warning {
        warnings.push(warning);
    }

    let obs_node = ObsNode {
        lifecycle_state: node
            .get("lifecycle_state")
            .and_then(Value::as_str)
            .map(str::to_owned),
        availability,
        sampled_at_unix_ms: node.get("sampled_at_unix_ms").and_then(Value::as_i64),
        errors,
        resources,
        recent_traces,
        workspace_count,
    };
    (obs_node, p1, warnings)
}

fn project_resources(
    sandbox_id: &str,
    resources: Option<&Value>,
    warnings: &mut Vec<String>,
) -> ObsResources {
    let latest = resources
        .and_then(|value| value.get("latest"))
        .and_then(project_resource_sample);
    let mut history = Vec::new();
    if let Some(samples) = resources
        .and_then(|value| value.get("history"))
        .and_then(Value::as_array)
    {
        if samples.len() > RESOURCE_HISTORY_CAP {
            warnings.push(format!(
                "resource history truncated for {sandbox_id}: kept {RESOURCE_HISTORY_CAP} of {}",
                samples.len()
            ));
        }
        history.extend(
            samples
                .iter()
                .take(RESOURCE_HISTORY_CAP)
                .filter_map(project_resource_sample),
        );
    }
    ObsResources { latest, history }
}

fn project_resource_sample(value: &Value) -> Option<ObsResourceSample> {
    let sample = value.as_object()?;
    Some(ObsResourceSample {
        sampled_at_unix_ms: sample.get("sampled_at_unix_ms").and_then(Value::as_i64),
        cgroup: sample.get("cgroup").cloned().unwrap_or(Value::Null),
        disk: sample.get("disk").cloned().unwrap_or(Value::Null),
    })
}

fn project_recent_traces(
    sandbox_id: &str,
    traces: Option<&Value>,
    warnings: &mut Vec<String>,
) -> Vec<ObsRecentTrace> {
    let Some(rows) = traces.and_then(Value::as_array) else {
        return Vec::new();
    };
    if rows.len() > RECENT_TRACE_CAP {
        warnings.push(format!(
            "recent_traces truncated for {sandbox_id}: kept {RECENT_TRACE_CAP} of {}",
            rows.len()
        ));
    }
    rows.iter()
        .take(RECENT_TRACE_CAP)
        .map(project_recent_trace)
        .collect()
}

fn project_recent_trace(value: &Value) -> ObsRecentTrace {
    let field = |key: &str| value.get(key).and_then(Value::as_str).map(str::to_owned);
    ObsRecentTrace {
        trace_id: field("trace_id"),
        kind: field("kind"),
        operation: field("operation"),
        status: field("status"),
        duration_ms: value.get("duration_ms").and_then(Value::as_i64),
        error_kind: field("error_kind"),
    }
}

fn project_p1(sandbox_id: &str, latest: Option<&ObsResourceSample>) -> (P1, Option<String>) {
    let Some(sample) = latest else {
        return (
            P1 {
                available: false,
                cpu_usage_usec: None,
                memory_current_bytes: None,
                memory_max_bytes: None,
                memory_max_unlimited: None,
                reason: Some("no resource sample".to_owned()),
            },
            Some(format!(
                "P1 unavailable for {sandbox_id}: no resource sample"
            )),
        );
    };
    let cgroup = &sample.cgroup;
    let cpu_usage_usec = cgroup.get("cpu_usage_usec").and_then(Value::as_i64);
    let memory_current_bytes = cgroup.get("memory_current_bytes").and_then(Value::as_i64);
    let memory_max_bytes = cgroup.get("memory_max_bytes").and_then(Value::as_i64);
    let memory_max_unlimited = cgroup.get("memory_max_unlimited").and_then(Value::as_bool);

    if cgroup.get("available").and_then(Value::as_bool) != Some(true) {
        let reason = match cgroup.get("error").and_then(Value::as_str) {
            Some(error) => format!("cgroup unavailable: {error}"),
            None => "cgroup unavailable".to_owned(),
        };
        return (
            P1 {
                available: false,
                cpu_usage_usec: None,
                memory_current_bytes: None,
                memory_max_bytes: None,
                memory_max_unlimited: None,
                reason: Some(reason),
            },
            Some(format!(
                "P1 unavailable for {sandbox_id}: cgroup unavailable"
            )),
        );
    }

    if cpu_usage_usec.is_none() && memory_current_bytes.is_none() {
        return (
            P1 {
                available: false,
                cpu_usage_usec,
                memory_current_bytes,
                memory_max_bytes,
                memory_max_unlimited,
                reason: Some("cgroup available but counters absent".to_owned()),
            },
            Some(format!("P1 partial for {sandbox_id}: counters absent")),
        );
    }

    (
        P1 {
            available: true,
            cpu_usage_usec,
            memory_current_bytes,
            memory_max_bytes,
            memory_max_unlimited,
            reason: None,
        },
        None,
    )
}

fn write_json_pretty<T: Serialize>(path: &Path, value: &T) -> io::Result<()> {
    let bytes = serde_json::to_vec_pretty(value).map_err(io::Error::other)?;
    fs::write(path, bytes)
}
