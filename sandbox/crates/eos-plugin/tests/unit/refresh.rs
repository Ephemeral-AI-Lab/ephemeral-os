use super::*;

#[test]
fn ack_rejects_wrong_manifest_key() {
    let ack = RefreshAck {
        manifest_key: "old".to_owned(),
        accepted: true,
        reason: None,
    };
    assert!(matches!(
        ack.require_manifest("new"),
        Err(PluginError::ProjectionStale(message)) if message.contains("expected new")
    ));
}

#[test]
fn swap_workspace_reports_target_manifest() {
    let request = RefreshRequest::SwapWorkspace {
        layer_paths: vec!["/layers/a".to_owned()],
        workspace_root: "/eos/plugin/workspace".to_owned(),
        manifest_key: "root@2".to_owned(),
    };
    assert_eq!(request.manifest_key(), Some("root@2"));
}
