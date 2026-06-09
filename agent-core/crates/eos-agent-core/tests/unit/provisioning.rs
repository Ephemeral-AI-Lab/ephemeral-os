use super::*;

// --- AC-eos-agent-core-07: provisioning binds the request sandbox.
//
// The real `origin=workflow` label logic lives in `eos-sandbox-host`'s
// `RequestSandboxProvisioner` (its `fresh_create_spec_has_request_name_and_labels`
// test) because its `ProviderAdapter` is sealed and cannot be mocked here. This
// runtime-level test proves the binding is threaded into the request row for both
// the explicit-id (whitespace-trimmed) and auto-create paths.

#[tokio::test]
async fn provisioning_binds_request_sandbox() {
    let (state, _dir) = build_test_state(Some(factory_from(vec![])), vec![root_agent()]).await;

    let explicit_id = RequestId::new_v4();
    run_request(&state, &explicit_id, "task", Some("  sb-explicit  "), None)
        .await
        .unwrap();
    let request = state
        .db
        .request_store
        .get(&explicit_id)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        request
            .sandbox_id
            .as_ref()
            .map(eos_types::SandboxId::as_str),
        Some("sb-explicit"),
        "explicit sandbox id is trimmed and bound"
    );

    let auto_id = RequestId::new_v4();
    run_request(&state, &auto_id, "task", None, None)
        .await
        .unwrap();
    let request = state.db.request_store.get(&auto_id).await.unwrap().unwrap();
    assert_eq!(
        request
            .sandbox_id
            .as_ref()
            .map(eos_types::SandboxId::as_str),
        Some("sb-test"),
        "auto path binds the provisioner-created sandbox id"
    );
}
