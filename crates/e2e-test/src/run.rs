//! Per-process E2E run identity and host-side report artifacts.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, PoisonError};
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{bail, Context, Result};
use host::e2e_support::DaemonContainer;
use host::e2e_support::{
    decode_trace_sidecar_base64, response_is_accepted, TraceStore, DAEMON_TRACE_SIDECAR_ENCODING,
    DAEMON_TRACE_SIDECAR_FIELD, DAEMON_TRACE_SIDECAR_SCHEMA,
};
use serde_json::{json, Value};
use trace::decode_trace_batch;

use crate::config::{ArtifactConfig, ArtifactDumpMode};
use crate::unique_suffix;

pub struct RunContext {
    run_id: String,
    suite: String,
    root_dir: PathBuf,
    trace_file: PathBuf,
    event_file: PathBuf,
    audit: TraceStore,
    write_lock: Mutex<()>,
    dump_mode: ArtifactDumpMode,
    daemon_log_dir: PathBuf,
    perf_dir: PathBuf,
}

impl RunContext {
    pub fn new(config_path: &Path, artifacts: &ArtifactConfig) -> Result<Self> {
        let run_id = run_id();
        let suite = suite_name(config_path);
        let root_dir = report_root(&run_id, artifacts);
        let trace_dir = child_dir(&root_dir, artifacts.trace_dir.as_ref(), "traces");
        let event_dir = child_dir(&root_dir, artifacts.event_dir.as_ref(), "events");
        let audit_dir = audit_dir(&root_dir, artifacts.audit_dir.as_ref(), &suite);
        let suite_dir = root_dir.join("suites").join(&suite);
        let daemon_log_dir = child_dir(&suite_dir, artifacts.daemon_log_dir.as_ref(), "containers");
        let perf_dir = child_dir(&root_dir, artifacts.perf_dir.as_ref(), "perf");
        for dir in [
            &root_dir,
            &trace_dir,
            &event_dir,
            &audit_dir,
            &suite_dir,
            &daemon_log_dir,
            &perf_dir,
        ] {
            fs::create_dir_all(dir)
                .with_context(|| format!("create e2e report directory {}", dir.display()))?;
        }
        let context = Self {
            trace_file: trace_dir.join(format!("{suite}.jsonl")),
            event_file: event_dir.join(format!("{suite}.jsonl")),
            audit: TraceStore::open(&audit_dir)
                .with_context(|| format!("open e2e trace audit store {}", audit_dir.display()))?,
            write_lock: Mutex::new(()),
            dump_mode: dump_mode(artifacts.dump_mode),
            daemon_log_dir: daemon_log_dir.clone(),
            perf_dir,
            run_id,
            suite,
            root_dir,
        };
        context.write_suite_summary(config_path, &daemon_log_dir)?;
        Ok(context)
    }

    #[must_use]
    pub fn run_id(&self) -> &str {
        &self.run_id
    }

    #[must_use]
    pub fn perf_dir(&self) -> &Path {
        &self.perf_dir
    }

    pub fn record_response(
        &self,
        op: &str,
        request_id: &str,
        caller_id: &str,
        container_name: &str,
        response: &Value,
    ) -> Result<()> {
        if !self.should_capture(response) {
            return Ok(());
        }
        let Some(sidecar) = trace_sidecar_bytes(response)? else {
            return Ok(());
        };
        let batch = decode_trace_batch(&sidecar).context("decode response trace sidecar")?;
        self.audit
            .ingest_trace_batch(container_name, &sidecar)
            .with_context(|| format!("ingest trace sidecar for {op} into audit store"))?;

        let _guard = self
            .write_lock
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let mut trace_file = append_jsonl(&self.trace_file)?;
        let mut event_file = append_jsonl(&self.event_file)?;
        for record in &batch.records {
            let trace_id = record.trace_id.to_string();
            write_json_line(
                &mut trace_file,
                &json!({
                    "schema": "eos.e2e.trace_record.v1",
                    "run_id": self.run_id,
                    "suite": self.suite,
                    "container": container_name,
                    "op": op,
                    "request_id": request_id,
                    "caller_id": caller_id,
                    "trace": record,
                }),
            )?;
            for event in &record.events {
                write_json_line(
                    &mut event_file,
                    &json!({
                        "schema": "eos.e2e.trace_event.v1",
                        "run_id": self.run_id,
                        "suite": self.suite,
                        "container": container_name,
                        "op": op,
                        "request_id": request_id,
                        "caller_id": caller_id,
                        "trace_id": trace_id,
                        "event": event,
                    }),
                )?;
            }
        }
        if batch.records.is_empty() || batch.dropped_traces > 0 {
            write_json_line(
                &mut trace_file,
                &json!({
                    "schema": "eos.e2e.trace_batch_summary.v1",
                    "run_id": self.run_id,
                    "suite": self.suite,
                    "container": container_name,
                    "op": op,
                    "request_id": request_id,
                    "caller_id": caller_id,
                    "record_count": batch.records.len(),
                    "dropped_traces": batch.dropped_traces,
                    "daemon_boot_id": batch.daemon_boot_id,
                }),
            )?;
        }
        Ok(())
    }

    pub fn record_daemon_log(&self, container: &DaemonContainer) -> Result<()> {
        if matches!(self.dump_mode, ArtifactDumpMode::Off) {
            return Ok(());
        }
        let dest = self
            .daemon_log_dir
            .join(container.name())
            .join("runtime.log");
        container
            .copy_daemon_log_to(&dest)
            .with_context(|| format!("copy daemon log for {}", container.name()))
    }

    fn should_capture(&self, response: &Value) -> bool {
        match self.dump_mode {
            ArtifactDumpMode::Off => false,
            ArtifactDumpMode::Failure => !response_is_accepted(response),
            ArtifactDumpMode::Always => true,
        }
    }

    fn write_suite_summary(&self, config_path: &Path, daemon_log_dir: &Path) -> Result<()> {
        let suite_dir = self.root_dir.join("suites").join(&self.suite);
        let summary_path = suite_dir.join("summary.json");
        let summary = json!({
            "schema": "eos.e2e.suite_report.v1",
            "run_id": self.run_id,
            "suite": self.suite,
            "config_path": config_path.display().to_string(),
            "started_at_unix_ms": now_ms(),
            "command": std::env::args().collect::<Vec<_>>(),
            "dump_mode": dump_mode_label(self.dump_mode),
            "artifacts": {
                "root_dir": self.root_dir.display().to_string(),
                "trace_file": self.trace_file.display().to_string(),
                "event_file": self.event_file.display().to_string(),
                "audit_db": self.audit.db_path().display().to_string(),
                "perf_dir": self.perf_dir.display().to_string(),
                "daemon_log_dir": daemon_log_dir.display().to_string(),
            },
        });
        fs::write(&summary_path, serde_json::to_vec_pretty(&summary)?)
            .with_context(|| format!("write e2e suite summary {}", summary_path.display()))
    }
}

fn trace_sidecar_bytes(response: &Value) -> Result<Option<Vec<u8>>> {
    let Some(object) = response.as_object() else {
        return Ok(None);
    };
    let Some(sidecar) = object.get(DAEMON_TRACE_SIDECAR_FIELD) else {
        return Ok(None);
    };
    let Value::Object(sidecar) = sidecar else {
        bail!("trace sidecar is not an object");
    };
    if sidecar.get("schema").and_then(Value::as_str) != Some(DAEMON_TRACE_SIDECAR_SCHEMA) {
        bail!("trace sidecar has invalid schema");
    }
    if sidecar.get("encoding").and_then(Value::as_str) != Some(DAEMON_TRACE_SIDECAR_ENCODING) {
        bail!("trace sidecar has invalid encoding");
    }
    if !sidecar.get("spool_pending").is_some_and(Value::is_boolean) {
        bail!("trace sidecar is missing spool_pending");
    }
    let data = sidecar
        .get("data")
        .and_then(Value::as_str)
        .context("trace sidecar is missing data")?;
    decode_trace_sidecar_base64(data)
        .context("trace sidecar has invalid base64")
        .map(Some)
}

fn append_jsonl(path: &Path) -> Result<std::fs::File> {
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .with_context(|| format!("open e2e JSONL artifact {}", path.display()))
}

fn write_json_line(file: &mut std::fs::File, value: &Value) -> Result<()> {
    serde_json::to_writer(&mut *file, value)?;
    writeln!(file)?;
    Ok(())
}

fn report_root(run_id: &str, artifacts: &ArtifactConfig) -> PathBuf {
    if let Some(root) = non_empty_env_path("EOS_E2E_REPORT_ROOT") {
        return root;
    }
    artifacts
        .root_dir
        .as_ref()
        .map(resolve_manifest_path)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("test-reports")
                .join("runs")
                .join(run_id)
        })
}

fn child_dir(root: &Path, override_dir: Option<&PathBuf>, default_name: &str) -> PathBuf {
    override_dir.map_or_else(
        || root.join(default_name),
        |dir| {
            if dir.is_absolute() {
                dir.clone()
            } else {
                root.join(dir)
            }
        },
    )
}

fn audit_dir(root: &Path, override_dir: Option<&PathBuf>, suite: &str) -> PathBuf {
    override_dir.map_or_else(
        || root.join("audit").join(suite),
        |dir| child_dir(root, Some(dir), "audit"),
    )
}

fn resolve_manifest_path(path: &PathBuf) -> PathBuf {
    if path.is_absolute() {
        path.clone()
    } else {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(path)
    }
}

fn dump_mode(configured: ArtifactDumpMode) -> ArtifactDumpMode {
    match std::env::var("EOS_E2E_ARTIFACTS") {
        Ok(value) if value.eq_ignore_ascii_case("off") => ArtifactDumpMode::Off,
        Ok(value) if value.eq_ignore_ascii_case("failure") => ArtifactDumpMode::Failure,
        Ok(value) if value.eq_ignore_ascii_case("always") => ArtifactDumpMode::Always,
        _ => configured,
    }
}

fn dump_mode_label(mode: ArtifactDumpMode) -> &'static str {
    match mode {
        ArtifactDumpMode::Off => "off",
        ArtifactDumpMode::Failure => "failure",
        ArtifactDumpMode::Always => "always",
    }
}

fn run_id() -> String {
    std::env::var("EOS_E2E_RUN_ID")
        .ok()
        .and_then(|raw| sanitize_id(&raw))
        .unwrap_or_else(|| format!("run-{}", unique_suffix()))
}

fn suite_name(config_path: &Path) -> String {
    config_path
        .parent()
        .and_then(Path::parent)
        .and_then(Path::file_name)
        .and_then(|name| name.to_str())
        .and_then(sanitize_id)
        .unwrap_or_else(|| "unknown-suite".to_owned())
}

fn sanitize_id(raw: &str) -> Option<String> {
    let value = raw
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch
            } else {
                '-'
            }
        })
        .collect::<String>();
    let value = value.trim_matches('-').to_owned();
    (!value.is_empty()).then_some(value)
}

fn non_empty_env_path(name: &str) -> Option<PathBuf> {
    std::env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_millis() as u64)
}
