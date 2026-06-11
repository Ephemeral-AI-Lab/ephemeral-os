use super::*;

type TestResult = std::result::Result<(), PluginError>;

fn manifest() -> PluginManifest {
    PluginManifest {
        plugin_id: "generic".to_owned(),
        plugin_version: "0.1.0".to_owned(),
        plugin_digest: "digest-a".to_owned(),
        package: PluginPackageManifest::default(),
        setup: Some(PluginSetupManifest {
            command: vec!["./setup.sh".to_owned()],
            working_dir: "runtime".to_owned(),
            setup_marker_digest: "setup-a".to_owned(),
            timeout_ms: 30_000,
        }),
        services: vec![PluginServiceManifest {
            service_id: "worker".to_owned(),
            service_profile_digest: "profile-a".to_owned(),
            service_mode: ServiceMode::WorkspaceSnapshotRefresh,
            refresh_strategy: RefreshStrategy::RemountWorkspaceAndNotify,
            command: vec!["generic-service".to_owned(), "--stdio".to_owned()],
            working_dir: Some("runtime".to_owned()),
            ppc_protocol_version: 1,
        }],
        operations: vec![PluginOperationManifest {
            op_name: "hover".to_owned(),
            intent: Intent::ReadOnly,
            auto_workspace_overlay: true,
            service_id: Some("worker".to_owned()),
            timeout_ms: Some(5_000),
        }],
    }
}

#[test]
fn validates_read_only_service_manifest() -> TestResult {
    manifest().validate()?;
    Ok(())
}

#[test]
fn rejects_read_only_op_without_service() {
    let mut manifest = manifest();
    manifest.operations[0].service_id = None;
    assert!(matches!(
        manifest.validate(),
        Err(PluginError::Manifest(message)) if message.contains("must reference")
    ));
}

#[test]
fn rejects_duplicate_operation_names() {
    let mut manifest = manifest();
    manifest.operations.push(manifest.operations[0].clone());
    assert!(matches!(
        manifest.validate(),
        Err(PluginError::Manifest(message)) if message.contains("duplicate op_name")
    ));
}

#[test]
fn plugin_id_matches_rust_name_rule() {
    let mut valid_manifest = manifest();
    valid_manifest.plugin_id = "_Lsp9".to_owned();
    assert!(valid_manifest.validate().is_ok());

    for invalid in ["my-plugin", "my.plugin", "9plugin", ""] {
        let mut manifest = manifest();
        manifest.plugin_id = invalid.to_owned();
        assert!(matches!(
            manifest.validate(),
            Err(PluginError::Manifest(message)) if message.contains("plugin_id")
        ));
    }
}

#[test]
fn op_name_is_only_non_empty_at_manifest_boundary() -> TestResult {
    let mut manifest = manifest();
    manifest.operations[0].op_name = "1 weird.op".to_owned();
    manifest.validate()?;

    manifest.operations[0].op_name = "   ".to_owned();
    assert!(matches!(
        manifest.validate(),
        Err(PluginError::Manifest(message)) if message.contains("op_name is required")
    ));
    Ok(())
}

#[test]
fn accepts_package_and_setup_contract() -> TestResult {
    let manifest = manifest();
    manifest.validate()?;
    assert_eq!(manifest.package.runtime_dir, "runtime");
    assert_eq!(manifest.package_marker_digest(), "digest-a");
    assert_eq!(manifest.setup_marker_digest(), Some("setup-a"));
    assert_eq!(PACKAGE_SHA256_MARKER, ".package-sha256");
    assert_eq!(SETUP_SHA256_MARKER, ".setup-sha256");
    Ok(())
}

#[test]
fn rejects_unknown_manifest_field() {
    let value = serde_json::json!({
        "plugin_id": "generic",
        "plugin_version": "0.1.0",
        "plugin_digest": "digest-a",
        "unexpected": true,
        "services": [],
        "operations": []
    });
    assert!(serde_json::from_value::<PluginManifest>(value).is_err());
}

#[test]
fn rejects_package_and_setup_paths_outside_package_tree() {
    let mut absolute_package = manifest();
    absolute_package.package.runtime_dir = "/runtime".to_owned();
    assert!(matches!(
        absolute_package.validate(),
        Err(PluginError::Manifest(message)) if message.contains("package.runtime_dir")
    ));

    let mut traversing_package = manifest();
    traversing_package.package.runtime_dir = "../runtime".to_owned();
    assert!(matches!(
        traversing_package.validate(),
        Err(PluginError::Manifest(message)) if message.contains("path traversal")
    ));

    let mut traversing_setup = manifest();
    traversing_setup
        .setup
        .as_mut()
        .expect("test fixture includes setup")
        .working_dir = "runtime/../escape".to_owned();
    assert!(matches!(
        traversing_setup.validate(),
        Err(PluginError::Manifest(message)) if message.contains("setup.working_dir")
    ));
}

#[test]
fn rejects_service_working_dir_outside_package_tree() {
    let mut manifest = manifest();
    manifest.services[0].working_dir = Some("/runtime".to_owned());
    assert!(matches!(
        manifest.validate(),
        Err(PluginError::Manifest(message)) if message.contains("service.working_dir")
    ));
}

#[test]
fn digest_fields_drive_marker_and_service_identity() -> TestResult {
    let base = manifest();
    let mut package_changed = base.clone();
    package_changed.plugin_digest = "digest-b".to_owned();
    assert_ne!(
        base.package_marker_digest(),
        package_changed.package_marker_digest()
    );

    let mut setup_changed = base.clone();
    setup_changed
        .setup
        .as_mut()
        .expect("test fixture includes setup")
        .setup_marker_digest = "setup-b".to_owned();
    assert_ne!(
        base.setup_marker_digest(),
        setup_changed.setup_marker_digest()
    );

    let mut service_changed = base.clone();
    service_changed.services[0].service_profile_digest = "profile-b".to_owned();
    assert_ne!(
        base.services[0].service_profile_digest,
        service_changed.services[0].service_profile_digest
    );
    Ok(())
}

#[test]
fn auto_overlay_write_requires_oneshot_service() {
    let mut manifest = manifest();
    manifest.operations[0].intent = Intent::WriteAllowed;
    manifest.operations[0].auto_workspace_overlay = true;
    assert!(matches!(
        manifest.validate(),
        Err(PluginError::Manifest(message)) if message.contains("oneshot_overlay")
    ));

    manifest.services[0].service_mode = ServiceMode::OneshotOverlay;
    manifest.services[0].refresh_strategy = RefreshStrategy::RestartService;
    assert!(manifest.validate().is_ok());
}

#[test]
fn oneshot_overlay_service_requires_command() {
    let mut manifest = manifest();
    manifest.services[0].service_mode = ServiceMode::OneshotOverlay;
    manifest.services[0].command.clear();
    assert!(matches!(
        manifest.validate(),
        Err(PluginError::Manifest(message)) if message.contains("requires a launch command")
    ));
}
