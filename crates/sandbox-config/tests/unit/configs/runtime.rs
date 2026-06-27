#[test]
fn config_prd_runtime_section_deserializes_and_validates() {
    prd_config().validate().expect("prd runtime config is valid");
}

#[test]
fn config_default_workspace_section_is_valid() {
    let config = WorkspaceConfig::default();
    config.validate().expect("default config is valid");
}

#[test]
fn config_validation_rejects_invalid_runtime_workspace_values() {
    let mut cfg = prd_config();
    cfg.workspace.layer_stack_root = std::path::PathBuf::from("relative");
    assert_invalid(cfg, "runtime.workspace.layer_stack_root");

    let mut cfg = prd_config();
    cfg.workspace.layer_stack_root = std::path::PathBuf::from("/");
    assert_invalid(cfg, "runtime.workspace.layer_stack_root");

    let mut cfg = prd_config();
    cfg.workspace.scratch_root = std::path::PathBuf::from("relative");
    assert_invalid(cfg, "runtime.workspace.scratch_root");

    let mut cfg = prd_config();
    cfg.workspace.scratch_root = std::path::PathBuf::from("/");
    assert_invalid(cfg, "runtime.workspace.scratch_root");

    let mut cfg = prd_config();
    cfg.workspace.exit_grace_s = -0.1;
    assert_invalid(cfg, "runtime.workspace.exit_grace_s");
}

fn prd_config() -> RuntimeConfig {
    crate::load_baseline()
        .expect("prd config loads")
        .section("runtime")
        .expect("runtime section deserializes")
}

fn assert_invalid(config: RuntimeConfig, field: &str) {
    let err = config.validate().expect_err("config should be invalid");
    let message = err.to_string();
    assert!(message.contains(field), "{message}");
}
