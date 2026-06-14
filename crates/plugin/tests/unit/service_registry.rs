use super::*;
use crate::service::PluginServiceKeyParts;
use crate::service::{RefreshStrategy, ServiceMode};

type TestResult = std::result::Result<(), PluginError>;

fn key(profile: &str) -> Result<PluginServiceKey> {
    PluginServiceKey::new(PluginServiceKeyParts {
        layer_stack_root: "/eos/plugin/layer-stack".to_owned(),
        workspace_root: "/eos/plugin/workspace".to_owned(),
        plugin_id: "generic".to_owned(),
        plugin_digest: "digest-a".to_owned(),
        service_id: "worker".to_owned(),
        service_profile_digest: profile.to_owned(),
        service_mode: ServiceMode::WorkspaceSnapshotRefresh,
        refresh_strategy: RefreshStrategy::RemountWorkspaceAndNotify,
    })
}

#[test]
fn ready_check_rejects_stale_manifest() -> TestResult {
    let mut status = PluginServiceStatus::new(key("profile-a")?);
    status.state = PluginServiceState::Ready;
    status.manifest_key = Some("manifest@1".to_owned());
    assert!(status.require_ready_on_manifest("manifest@2").is_err());
    Ok(())
}
