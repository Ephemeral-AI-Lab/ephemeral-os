#[test]
fn config_prd_daemon_section_deserializes_and_validates() {
    let cfg = prd_config();
    cfg.validate().expect("prd daemon config is valid");
}

#[test]
fn config_prd_daemon_section_does_not_carry_dynamic_sandbox_identity() {
    let config_path = crate::ConfigPath::prd().expect("prd config path resolves");
    let raw = std::fs::read_to_string(config_path.as_path()).expect("prd config is readable");

    assert!(
        !raw.contains("sandbox_id"),
        "static daemon YAML must not contain dynamic sandbox identity"
    );
}

#[test]
fn daemon_config_accepts_daemon_owned_telemetry_extension() {
    let cfg = daemon_config(
        r#"
server:
  socket_path: /eos/runtime/daemon/runtime.sock
  pid_path: /eos/runtime/daemon/runtime.pid
  max_worker_threads: 2
commands:
  scratch_root: /eos/scratch/commands
cgroup_monitor:
  enabled: true
  sample_interval_ms: 1000
  retained_samples_per_target: 10
  include_pids: true
  include_pressure: true
  include_disk: true
telemetry:
  enabled: true
  service_name: sandbox-daemon
  level: info
  sink:
    kind: local_json
    stream: stdout
idle_workspace_eviction:
  interval_ms: 500
"#,
    );

    cfg.validate()
        .expect("daemon config with daemon-owned telemetry extension validates");
}

#[test]
fn daemon_config_accepts_daemon_owned_otlp_telemetry_extension() {
    let cfg = daemon_config(
        r#"
server:
  socket_path: /eos/runtime/daemon/runtime.sock
  pid_path: /eos/runtime/daemon/runtime.pid
  max_worker_threads: 2
commands:
  scratch_root: /eos/scratch/commands
cgroup_monitor:
  enabled: true
  sample_interval_ms: 1000
  retained_samples_per_target: 10
  include_pids: true
  include_pressure: true
  include_disk: true
telemetry:
  enabled: true
  service_name: sandbox-daemon
  level: info
  export_logs: true
  sink:
    kind: otlp
    endpoint: http://collector:4318
    protocol: http
    timeout_ms: 1000
    queue_size: 2048
idle_workspace_eviction:
  interval_ms: 500
"#,
    );

    cfg.validate()
        .expect("daemon config with daemon-owned OTLP telemetry extension validates");
}

#[test]
fn config_validation_rejects_invalid_daemon_values() {
    let mut cfg = prd_config();
    cfg.server.max_worker_threads = 0;
    assert_invalid(cfg, "daemon.server.max_worker_threads");

    let mut cfg = prd_config();
    cfg.commands.scratch_root = std::path::PathBuf::from("/");
    assert_invalid(cfg, "daemon.commands.scratch_root");

    let mut cfg = prd_config();
    cfg.cgroup_monitor.sample_interval_ms = 0;
    assert_invalid(cfg, "daemon.cgroup_monitor.sample_interval_ms");

    let mut cfg = prd_config();
    cfg.cgroup_monitor.retained_samples_per_target = 0;
    assert_invalid(cfg, "daemon.cgroup_monitor.retained_samples_per_target");
}

fn prd_config() -> DaemonConfig {
    crate::load_baseline()
        .expect("prd config loads")
        .section("daemon")
        .expect("daemon section deserializes")
}

fn assert_invalid(config: DaemonConfig, field: &str) {
    let err = config.validate().expect_err("config should be invalid");
    let message = err.to_string();
    assert!(message.contains(field), "{message}");
}

fn daemon_config(yaml: &str) -> DaemonConfig {
    serde_yaml_ng::from_str(yaml).expect("daemon config deserializes")
}
