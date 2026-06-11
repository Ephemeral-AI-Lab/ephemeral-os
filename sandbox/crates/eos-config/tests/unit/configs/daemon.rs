use std::path::Path;

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
    cfg.command_sessions.cancel_wait_ms = 0;
    assert_invalid(cfg, "daemon.command_sessions.cancel_wait_ms");

    let mut cfg = prd_config();
    cfg.command_sessions.default_timeout_s = 0;
    assert_invalid(cfg, "daemon.command_sessions.default_timeout_s");

    let mut cfg = prd_config();
    cfg.plugin.ppc_root = PathBuf::from("relative");
    assert_invalid(cfg, "daemon.plugin.ppc_root");

    let mut cfg = prd_config();
    cfg.layer_stack.auto_squash_max_depth = 0;
    assert_invalid(cfg, "daemon.layer_stack.auto_squash_max_depth");

    let mut cfg = prd_config();
    cfg.files.max_write_bytes = 0;
    assert_invalid(cfg, "daemon.files.max_write_bytes");
}

#[test]
fn config_plugin_child_module_does_not_own_config_rs() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let daemon_dir = manifest_dir
        .ancestors()
        .nth(2)
        .expect("eos-config lives below sandbox/crates")
        .join("crates/eos-daemon");
    assert!(
        !daemon_dir.join("src/services/plugins/config.rs").exists(),
        "plugin config must be owned by eos-config/src/configs/daemon.rs"
    );
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
