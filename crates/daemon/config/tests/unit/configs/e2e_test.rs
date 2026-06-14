use super::*;

#[test]
fn prd_e2e_section_deserializes_and_validates() {
    let doc = crate::load_prd().expect("prd config loads");
    let cfg = EosE2eTestConfig::from_document(&doc).expect("prd e2e_test config is valid");
    assert!(
        cfg.docker.remote_config_path.is_absolute(),
        "default remote_config_path is absolute"
    );
    assert!(
        cfg.docker.privileged,
        "prd config keeps privileged Docker bootstrap for mount setup"
    );
}

#[test]
fn validation_rejects_invalid_e2e_values() {
    let mut cfg = prd_config();
    cfg.docker.image.clear();
    assert_invalid(cfg, "e2e_test.docker.image");

    let mut cfg = prd_config();
    cfg.docker.remote_eosd_path = PathBuf::from("relative");
    assert_invalid(cfg, "e2e_test.docker.remote_eosd_path");

    let mut cfg = prd_config();
    cfg.docker.remote_config_path = PathBuf::from("relative");
    assert_invalid(cfg, "e2e_test.docker.remote_config_path");

    let mut cfg = prd_config();
    cfg.docker.tcp_port = 0;
    assert_invalid(cfg, "e2e_test.docker.tcp_port");

    let mut cfg = prd_config();
    cfg.pool.sandboxes = 0;
    assert_invalid(cfg, "e2e_test.pool.sandboxes");

    let mut cfg = prd_config();
    cfg.timeouts.ready_s = 0;
    assert_invalid(cfg, "e2e_test.timeouts.ready_s");

    let mut cfg = prd_config();
    cfg.workload.concurrency_levels = vec![1, 0, 3];
    assert_invalid(cfg, "e2e_test.workload.concurrency_levels");

    let mut cfg = prd_config();
    cfg.workload.concurrency_levels = vec![1, 3, 3];
    assert_invalid(cfg, "duplicate level 3");

    let mut cfg = prd_config();
    cfg.workload.write_iterations = 0;
    assert_invalid(cfg, "e2e_test.workload.write_iterations");
}

fn prd_config() -> EosE2eTestConfig {
    let doc = crate::load_prd().expect("prd config loads");
    EosE2eTestConfig::from_document(&doc).expect("e2e_test section deserializes")
}

fn assert_invalid(config: EosE2eTestConfig, field: &str) {
    let err = config.validate().expect_err("config should be invalid");
    let message = err.to_string();
    assert!(message.contains(field), "{message}");
}
