use super::*;

type TestResult = std::result::Result<(), PluginError>;

#[test]
fn service_key_includes_profile_and_refresh_strategy() -> TestResult {
    let base = PluginServiceKey::new(parts("profile-a"))?;
    let mut changed = base.clone();
    changed.service_profile_digest = "profile-b".to_owned();
    assert_ne!(base, changed);

    let mut changed_strategy = base.clone();
    changed_strategy.refresh_strategy = RefreshStrategy::RestartService;
    assert_ne!(base, changed_strategy);
    Ok(())
}

#[test]
fn service_key_rejects_relative_workspace_paths() {
    let mut parts = parts("profile-a");
    parts.layer_stack_root = "relative".to_owned();
    assert!(matches!(
        PluginServiceKey::new(parts),
        Err(PluginError::Manifest(message)) if message.contains("absolute")
    ));
}

#[test]
fn plugin_id_uses_rust_name_rule() {
    assert!(validate_plugin_id("plugin_id", "_ok").is_ok());
    assert!(validate_plugin_id("plugin_id", "Generic").is_ok());
    assert!(is_valid_plugin_name("_x9"));
    assert!(!is_valid_plugin_name("9plugin"));
    assert!(!is_valid_plugin_name(""));
    assert!(!is_valid_plugin_name("ls-p"));
    assert!(matches!(
        validate_plugin_id("plugin_id", "my-plugin"),
        Err(PluginError::Manifest(message)) if message.contains("must match")
    ));
    assert!(matches!(
        validate_plugin_id("plugin_id", "my.plugin"),
        Err(PluginError::Manifest(message)) if message.contains("must match")
    ));
}

fn parts(profile: &str) -> PluginServiceKeyParts {
    PluginServiceKeyParts {
        layer_stack_root: "/eos/plugin/layer-stack".to_owned(),
        workspace_root: "/eos/plugin/workspace".to_owned(),
        plugin_id: "generic".to_owned(),
        plugin_digest: "digest-a".to_owned(),
        service_id: "worker".to_owned(),
        service_profile_digest: profile.to_owned(),
        service_mode: ServiceMode::WorkspaceSnapshotRefresh,
        refresh_strategy: RefreshStrategy::RemountWorkspaceAndNotify,
    }
}
