use std::io;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::Result;
use serde_json::{json, Value};
use tokio::runtime::Runtime;
use tracing_subscriber::fmt::MakeWriter;

use crate::server::{SandboxDaemonServer, ServerConfig};
use crate::telemetry::{
    DaemonServeMode, TelemetryConfig, TelemetryOutputStream, TelemetrySink,
};

#[test]
fn local_json_telemetry_formats_span_close_records() -> Result<()> {
    let writer = CaptureWriter::default();
    let runtime = Runtime::new()?;
    let server = test_server(Some("sbox-json"));
    let request = json!({
        "op": "unknown_op",
        "request_id": "req-json",
        "scope": { "kind": "sandbox", "sandbox_id": "scope-sbox" },
        "args": {}
    });
    let request_bytes = serde_json::to_vec(&request)?;

    let response = crate::telemetry::with_test_json_subscriber(
        &local_json_telemetry(TelemetryOutputStream::Stdout),
        writer.clone(),
        || runtime.block_on(server.dispatch_bytes(request_bytes, false)),
    )?;

    assert_eq!(response["error"]["kind"], "unknown_op");
    let output = writer.output();
    let lines = json_lines(&output);
    assert!(
        lines.iter().any(|line| line["level"] == "INFO"),
        "expected JSON info line in {output}"
    );
    assert!(
        output.contains("\"daemon.request\""),
        "daemon.request span should be present in {output}"
    );
    assert!(
        output.contains("time.busy") && output.contains("time.idle"),
        "span close timing fields should be present in {output}"
    );
    Ok(())
}

#[test]
fn daemon_request_span_records_dynamic_sandbox_id() -> Result<()> {
    let writer = CaptureWriter::default();
    let runtime = Runtime::new()?;
    let server = test_server(Some("dynamic-sbox"));
    let request = json!({
        "op": "unknown_op_OPERATION_SECRET_SENTINEL",
        "request_id": "REQUEST_ID_SECRET_SENTINEL",
        "scope": { "kind": "sandbox", "sandbox_id": "scope-sbox" },
        "args": {}
    });
    let request_bytes = serde_json::to_vec(&request)?;

    crate::telemetry::with_test_json_subscriber(
        &local_json_telemetry(TelemetryOutputStream::Stderr),
        writer.clone(),
        || runtime.block_on(server.dispatch_bytes(request_bytes, false)),
    )?;

    let output = writer.output();
    assert!(output.contains("dynamic-sbox"), "{output}");
    assert!(output.contains("unknown_op"), "{output}");
    assert!(output.contains("sandbox"), "{output}");
    assert!(
        !output.contains("REQUEST_ID_SECRET_SENTINEL"),
        "raw request_id must not appear in telemetry: {output}"
    );
    assert!(
        !output.contains("unknown_op_OPERATION_SECRET_SENTINEL"),
        "raw unknown operation must not appear in telemetry: {output}"
    );
    Ok(())
}

#[test]
fn pre_decode_failure_telemetry_is_sanitized() -> Result<()> {
    let writer = CaptureWriter::default();
    let runtime = Runtime::new()?;
    let server = test_server(Some("dynamic-sbox"));
    let raw = br#"{"op":"exec_command","_sandbox_daemon_auth_token":"SECRET_AUTH_SENTINEL""#.to_vec();

    let response = crate::telemetry::with_test_json_subscriber(
        &local_json_telemetry(TelemetryOutputStream::Stdout),
        writer.clone(),
        || runtime.block_on(server.dispatch_bytes(raw, true)),
    )?;

    assert_eq!(response["error"]["kind"], "bad_json");
    let output = writer.output();
    assert!(output.contains("bad_json"), "{output}");
    assert!(
        !output.contains("SECRET_AUTH_SENTINEL"),
        "raw auth-like payload must not appear in telemetry: {output}"
    );
    assert!(
        !output.contains("_sandbox_daemon_auth_token"),
        "auth field names from raw payload must not appear in telemetry: {output}"
    );
    Ok(())
}

#[test]
fn telemetry_disabled_config_deserializes_without_sink() {
    let cfg = telemetry_config(
        r#"
enabled: false
service_name: sandbox-daemon
level: info
"#,
    );

    assert!(!cfg.enabled);
    assert!(cfg.sink.is_none());
    cfg.validate().expect("disabled telemetry validates");
    cfg.validate_for_serve_mode(DaemonServeMode::Spawn)
        .expect("disabled telemetry is valid under spawned serve");
}

#[test]
fn telemetry_section_defaults_to_disabled_when_omitted() {
    let cfg = telemetry_section(
        r#"
server:
  socket_path: /eos/runtime/daemon/runtime.sock
  pid_path: /eos/runtime/daemon/runtime.pid
  max_worker_threads: 2
"#,
    );

    assert_eq!(cfg, TelemetryConfig::default());
    cfg.validate()
        .expect("omitted telemetry defaults to disabled config");
}

#[test]
fn telemetry_loads_from_prd_config_document() {
    let config_path = sandbox_config::ConfigPath::prd().expect("prd config path resolves");
    let doc = sandbox_config::load_path(config_path.as_path()).expect("prd config loads");

    let cfg = crate::telemetry::from_config_document(&doc).expect("daemon telemetry deserializes");

    assert_eq!(cfg, TelemetryConfig::default());
    cfg.validate().expect("prd telemetry validates");
}

#[test]
fn telemetry_local_json_accepts_stdout_and_stderr_in_foreground_mode() {
    for stream in ["stdout", "stderr"] {
        let cfg = telemetry_config(&format!(
            r#"
enabled: true
service_name: sandbox-daemon
level: info
sink:
  kind: local_json
  stream: {stream}
"#
        ));

        assert!(matches!(cfg.sink, Some(TelemetrySink::LocalJson { .. })));
        cfg.validate_for_serve_mode(DaemonServeMode::Foreground)
            .expect("local json stream is valid in foreground mode");
    }
}

#[test]
fn telemetry_rejects_invalid_stream() {
    let err = telemetry_deserialize_error(
        r#"
enabled: true
service_name: sandbox-daemon
level: info
sink:
  kind: local_json
  stream: file
"#,
    );

    assert!(
        err.contains("stream") || err.contains("file"),
        "unexpected error: {err}"
    );
}

#[test]
fn telemetry_rejects_invalid_level() {
    let cfg = telemetry_config(
        r#"
enabled: true
service_name: sandbox-daemon
level: verbose
sink:
  kind: local_json
  stream: stdout
"#,
    );

    let err = cfg.validate().expect_err("invalid telemetry level rejected");

    assert_eq!(err.field, "daemon.telemetry.level");
}

#[test]
fn telemetry_rejects_unknown_sink_kind() {
    let err = telemetry_deserialize_error(
        r#"
enabled: true
service_name: sandbox-daemon
level: info
sink:
  kind: otlp
  stream: stdout
"#,
    );

    assert!(
        err.contains("otlp") || err.contains("kind"),
        "unexpected error: {err}"
    );
}

#[test]
fn telemetry_enabled_config_requires_sink() {
    let cfg = telemetry_config(
        r#"
enabled: true
service_name: sandbox-daemon
level: info
"#,
    );

    let err = cfg
        .validate()
        .expect_err("enabled telemetry without sink is rejected");

    assert_eq!(err.field, "daemon.telemetry.sink");
}

#[test]
fn telemetry_rejects_local_json_in_spawn_mode() {
    let cfg = telemetry_config(
        r#"
enabled: true
service_name: sandbox-daemon
level: info
sink:
  kind: local_json
  stream: stderr
"#,
    );

    let err = cfg
        .validate_for_serve_mode(DaemonServeMode::Spawn)
        .expect_err("local json stdout/stderr is foreground-only");

    assert_eq!(err.field, "daemon.telemetry.sink");
}

#[derive(Clone, Default)]
struct CaptureWriter {
    bytes: Arc<Mutex<Vec<u8>>>,
}

impl CaptureWriter {
    fn output(&self) -> String {
        String::from_utf8(self.bytes.lock().expect("capture lock").clone())
            .expect("telemetry is utf8")
    }
}

impl<'writer> MakeWriter<'writer> for CaptureWriter {
    type Writer = Capture;

    fn make_writer(&'writer self) -> Self::Writer {
        Capture {
            bytes: Arc::clone(&self.bytes),
        }
    }
}

struct Capture {
    bytes: Arc<Mutex<Vec<u8>>>,
}

impl io::Write for Capture {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.bytes.lock().expect("capture lock").extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn local_json_telemetry(stream: TelemetryOutputStream) -> TelemetryConfig {
    TelemetryConfig {
        enabled: true,
        service_name: "sandbox-daemon".to_owned(),
        level: "info".to_owned(),
        sink: Some(TelemetrySink::LocalJson { stream }),
    }
}

fn telemetry_section(yaml: &str) -> TelemetryConfig {
    #[derive(serde::Deserialize)]
    struct DaemonTelemetrySection {
        #[serde(default)]
        telemetry: TelemetryConfig,
    }
    serde_yaml_ng::from_str::<DaemonTelemetrySection>(yaml)
        .expect("daemon telemetry section deserializes")
        .telemetry
}

fn telemetry_config(yaml: &str) -> TelemetryConfig {
    serde_yaml_ng::from_str(yaml).expect("telemetry config deserializes")
}

fn telemetry_deserialize_error(yaml: &str) -> String {
    serde_yaml_ng::from_str::<TelemetryConfig>(yaml)
        .expect_err("telemetry config should fail to deserialize")
        .to_string()
}

fn json_lines(output: &str) -> Vec<Value> {
    output
        .lines()
        .map(|line| serde_json::from_str(line).expect("telemetry line is json"))
        .collect()
}

fn test_server(sandbox_id: Option<&str>) -> SandboxDaemonServer {
    SandboxDaemonServer::new(
        ServerConfig {
            socket_path: PathBuf::from("/tmp/sandbox-daemon-test.sock"),
            pid_path: PathBuf::from("/tmp/sandbox-daemon-test.pid"),
            tcp_host: None,
            tcp_port: None,
            auth_token: Some("configured-token".to_owned()),
            sandbox_id: sandbox_id.map(str::to_owned),
        },
        Arc::new(test_operations()),
    )
}

fn test_operations() -> sandbox_runtime::SandboxRuntimeOperations {
    let base = temp_root("sandbox-daemon-telemetry");
    let workspace_root = base.join("workspace");
    let layer_stack_root = base.join("layer-stack");
    std::fs::create_dir_all(&workspace_root).expect("create telemetry test workspace");
    sandbox_runtime_layerstack::build_workspace_base(&layer_stack_root, &workspace_root, false)
        .expect("build telemetry test layerstack workspace base");

    sandbox_runtime::SandboxRuntimeOperations::from_config(sandbox_runtime::SandboxRuntimeConfig {
        workspace: sandbox_runtime::WorkspaceRuntimeConfig {
            workspace_root,
            layer_stack_root,
            scratch_root: base.join("workspace-scratch"),
            caps: sandbox_runtime::WorkspaceResourceCaps {
                ttl_s: 60.0,
                total_cap: 2,
                upperdir_bytes: 1024 * 1024,
                memavail_fraction: 0.5,
                setup_timeout_s: 1.0,
                exit_grace_s: 0.1,
                rfc1918_egress: sandbox_runtime::Rfc1918Egress::Allow,
            },
        },
        command: sandbox_runtime::CommandRuntimeConfig {
            scratch_root: base.join("command-scratch"),
        },
        cgroup_monitor: sandbox_runtime::CgroupMonitorRuntimeConfig {
            enabled: false,
            sample_interval_ms: 1000,
            retained_samples_per_target: 10,
            include_pids: false,
            include_pressure: false,
            include_disk: false,
        },
    })
}

fn temp_root(label: &str) -> PathBuf {
    static NEXT_TEMP_ROOT_ID: AtomicU64 = AtomicU64::new(1);

    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system time after epoch")
        .as_nanos();
    let unique_id = NEXT_TEMP_ROOT_ID.fetch_add(1, Ordering::Relaxed);
    std::env::temp_dir().join(format!(
        "{label}-{}-{nanos}-{unique_id}",
        std::process::id()
    ))
}
