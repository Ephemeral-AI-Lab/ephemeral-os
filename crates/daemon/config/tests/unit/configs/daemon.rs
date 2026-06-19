use super::*;

#[test]
fn config_prd_daemon_section_deserializes_and_validates() {
    prd_config().validate().expect("prd daemon config is valid");
}

#[test]
fn config_validation_rejects_invalid_daemon_values() {
    let mut cfg = prd_config();
    cfg.server.max_worker_threads = 0;
    assert_invalid(cfg, "daemon.server.max_worker_threads");

    let mut cfg = prd_config();
    cfg.inflight.ttl_s = 0.0;
    assert_invalid(cfg, "daemon.inflight.ttl_s");

    let mut cfg = prd_config();
    cfg.commands.cancel_wait_ms = 0;
    assert_invalid(cfg, "daemon.commands.cancel_wait_ms");

    let mut cfg = prd_config();
    cfg.commands.default_timeout_s = 0;
    assert_invalid(cfg, "daemon.commands.default_timeout_s");

    let mut cfg = prd_config();
    cfg.commands.scratch_root = std::path::PathBuf::from("/");
    assert_invalid(cfg, "daemon.commands.scratch_root");

    let mut cfg = prd_config();
    cfg.commands.ignored_capture.max_files = 0;
    assert_invalid(cfg, "daemon.commands.ignored_capture.max_files");

    let mut cfg = prd_config();
    cfg.commands.ignored_capture.max_bytes = 0;
    assert_invalid(cfg, "daemon.commands.ignored_capture.max_bytes");

    let mut cfg = prd_config();
    cfg.commands.ignored_capture.max_file_bytes = 0;
    assert_invalid(cfg, "daemon.commands.ignored_capture.max_file_bytes");

    let mut cfg = prd_config();
    cfg.commands.ignored_capture.max_file_bytes = cfg.commands.ignored_capture.max_bytes + 1;
    assert_invalid(cfg, "daemon.commands.ignored_capture.max_file_bytes");

    let mut cfg = prd_config();
    cfg.commands.ignored_capture.max_file_bytes = 1024;
    cfg.commands.ignored_capture.spool_threshold_bytes = cfg.commands.ignored_capture.max_bytes;
    assert_invalid(cfg, "daemon.commands.ignored_capture.spool_threshold_bytes");

    let mut cfg = prd_config();
    cfg.commands
        .ignored_capture
        .max_metadata_capture_duration_ms = 0;
    assert_invalid(
        cfg,
        "daemon.commands.ignored_capture.max_metadata_capture_duration_ms",
    );

    let mut cfg = prd_config();
    cfg.layer_stack.auto_squash_max_depth = 0;
    assert_invalid(cfg, "daemon.layer_stack.auto_squash_max_depth");
}

fn prd_config() -> DaemonConfig {
    crate::load_prd()
        .expect("prd config loads")
        .section("daemon")
        .expect("daemon section deserializes")
}

fn assert_invalid(config: DaemonConfig, field: &str) {
    let err = config.validate().expect_err("config should be invalid");
    let message = err.to_string();
    assert!(message.contains(field), "{message}");
}
