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
fn config_validation_rejects_invalid_daemon_values() {
    let mut cfg = prd_config();
    cfg.server.max_worker_threads = 0;
    assert_invalid(cfg, "daemon.server.max_worker_threads");
}

#[test]
fn config_server_limits_default_to_shipped_policy() {
    // prd.yml carries none of the limit keys, so the section must load to
    // today's exact constants.
    let cfg = prd_config();
    assert_eq!(cfg.server.max_concurrent_connections, 256);
    assert_eq!(cfg.server.max_request_bytes, 16 * 1024 * 1024);
    assert!((cfg.server.request_read_timeout_s - 30.0).abs() < f64::EPSILON);
}

#[test]
fn config_http_forward_defaults_to_shipped_policy() {
    let cfg = prd_config();
    assert_eq!(cfg.http.forward, DaemonHttpForwardConfig::default());
    assert!((cfg.http.forward.connect_timeout_s - 10.0).abs() < f64::EPSILON);
    assert!((cfg.http.forward.response_timeout_s - 30.0).abs() < f64::EPSILON);
}

#[test]
fn config_server_limits_and_forward_overrides_deserialize() {
    let cfg = daemon_config(
        "    max_concurrent_connections: 2
    max_request_bytes: 65536
    request_read_timeout_s: 5.5
  http:
    forward:
      connect_timeout_s: 1.5
      response_timeout_s: 0.1
",
    )
    .expect("daemon overrides deserialize");
    cfg.validate().expect("daemon overrides are valid");
    assert_eq!(cfg.server.max_concurrent_connections, 2);
    assert_eq!(cfg.server.max_request_bytes, 65536);
    assert!((cfg.server.request_read_timeout_s - 5.5).abs() < f64::EPSILON);
    assert!((cfg.http.forward.connect_timeout_s - 1.5).abs() < f64::EPSILON);
    assert!((cfg.http.forward.response_timeout_s - 0.1).abs() < f64::EPSILON);
}

#[test]
fn config_daemon_http_rejects_unknown_keys() {
    // `daemon.http` exists for the forward proxy only; the export spool
    // stream was removed in favor of RPC paging (phase-1 drift note), so an
    // `export` subsection must fail loudly instead of loading into nothing.
    let error = daemon_config("  http:\n    export:\n      frame_bytes: 4096\n")
        .expect_err("daemon.http.export must be rejected");
    assert!(error.to_string().contains("export"), "{error}");

    let error = daemon_config("  http:\n    forward:\n      idle_timeout_s: 1\n")
        .expect_err("unknown daemon.http.forward key must be rejected");
    assert!(error.to_string().contains("idle_timeout_s"), "{error}");
}

#[test]
fn config_validation_rejects_server_limit_edge_values() {
    let mut cfg = prd_config();
    cfg.server.max_concurrent_connections = 0;
    assert_invalid(cfg, "daemon.server.max_concurrent_connections");

    let mut cfg = prd_config();
    cfg.server.max_request_bytes = 65535;
    assert_invalid(cfg, "daemon.server.max_request_bytes");

    let mut cfg = prd_config();
    cfg.server.max_request_bytes = 65536;
    cfg.validate().expect("max_request_bytes 65536 is valid");

    let mut cfg = prd_config();
    cfg.server.request_read_timeout_s = 0.0;
    assert_invalid(cfg, "daemon.server.request_read_timeout_s");
}

#[test]
fn config_validation_rejects_forward_timeout_edge_values() {
    let mut cfg = prd_config();
    cfg.http.forward.connect_timeout_s = 0.0;
    assert_invalid(cfg, "daemon.http.forward.connect_timeout_s");

    let mut cfg = prd_config();
    cfg.http.forward.response_timeout_s = -1.0;
    assert_invalid(cfg, "daemon.http.forward.response_timeout_s");
}

fn daemon_config(extra_yaml: &str) -> Result<DaemonConfig, crate::ConfigError> {
    let yaml = format!(
        "daemon:
  server:
    socket_path: /eos/runtime/daemon/runtime.sock
    pid_path: /eos/runtime/daemon/runtime.pid
    max_worker_threads: 32
{extra_yaml}"
    );
    crate::ConfigDocument::parse(std::path::Path::new("<test>"), &yaml)?.section("daemon")
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
