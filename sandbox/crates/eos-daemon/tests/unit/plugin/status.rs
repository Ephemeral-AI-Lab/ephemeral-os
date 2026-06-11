use super::super::refresh::WORKSPACE_SNAPSHOT_REFRESH_OP;
use super::support::*;

use crate::wire::Request;
use serde_json::{json, Value};

#[test]
fn status_probe_services_sends_health_request() -> TestResult {
    let daemon = TestDaemon::new();
    let (layer_stack_root, workspace_root) = test_bound_workspace("status-health-ok")?;
    let ensure = daemon.dispatch(&Request {
        op: "sandbox.plugin.ensure".to_owned(),
        invocation_id: "plugin-ensure-health-ok".to_owned(),
        args: json!({
            "manifest": generic_service_manifest("digest-a", "hover"),
            "layer_stack_root": layer_stack_root.to_string_lossy().into_owned(),
            "workspace_root": workspace_root.to_string_lossy().into_owned()
        }),
    });
    assert_eq!(ensure["success"], true);

    let (client_stream, mut server_stream) = ppc_stream_pair()?;
    daemon
        .plugin()
        .register_ppc_client_for_tests("plugin.generic.hover", client_stream)?;
    let (_service_instance_id, manifest_key) =
        attach_service_snapshot_for_tests(daemon.plugin(), "plugin.generic.hover")?;
    let expected_manifest_key = manifest_key.clone();
    let server = std::thread::spawn(move || -> TestResult {
        let request = read_ppc_request(&mut server_stream, "read health request")?;
        assert_eq!(request.op, WORKSPACE_SNAPSHOT_REFRESH_OP);
        let body: Value = serde_json::from_str(&request.body)?;
        assert_eq!(body["type"], "health");
        assert_eq!(body["manifest_key"], expected_manifest_key);
        write_ppc_reply_json_result(
            &mut server_stream,
            request.message_id,
            &json!({"manifest_key": expected_manifest_key, "accepted": true}),
        )?;
        Ok(())
    });

    let status = daemon.dispatch(&Request {
        op: "sandbox.plugin.status".to_owned(),
        invocation_id: "plugin-status-health-ok".to_owned(),
        args: json!({"probe_services": true, "probe_timeout_ms": 1000}),
    });
    assert_eq!(status["success"], true);
    assert_eq!(status["service_health"][0]["success"], true);
    assert_eq!(status["service_health"][0]["service_id"], "worker");
    assert_eq!(status["service_health"][0]["manifest_key"], manifest_key);
    assert_eq!(status["loaded_plugins"][0]["services"][0]["state"], "ready");
    assert_eq!(
        status["connected_ppc_routes"],
        json!(["plugin.generic.hover"])
    );
    join_test_thread(server, "server thread panicked")?;
    remove_test_tree(&layer_stack_root)?;
    Ok(())
}

#[test]
fn status_probe_failure_drops_connected_service() -> TestResult {
    let daemon = TestDaemon::new();
    let (layer_stack_root, workspace_root) = test_bound_workspace("status-health-fail")?;
    let ensure = daemon.dispatch(&Request {
        op: "sandbox.plugin.ensure".to_owned(),
        invocation_id: "plugin-ensure-health-fail".to_owned(),
        args: json!({
            "manifest": generic_service_manifest("digest-a", "hover"),
            "layer_stack_root": layer_stack_root.to_string_lossy().into_owned(),
            "workspace_root": workspace_root.to_string_lossy().into_owned()
        }),
    });
    assert_eq!(ensure["success"], true);

    let (client_stream, mut server_stream) = ppc_stream_pair()?;
    daemon
        .plugin()
        .register_ppc_client_for_tests("plugin.generic.hover", client_stream)?;
    let (service_instance_id, manifest_key) =
        attach_service_snapshot_for_tests(daemon.plugin(), "plugin.generic.hover")?;
    let server = std::thread::spawn(move || -> TestResult {
        let request = read_ppc_request(&mut server_stream, "read health request")?;
        assert_eq!(request.op, WORKSPACE_SNAPSHOT_REFRESH_OP);
        write_ppc_reply_json_result(
            &mut server_stream,
            request.message_id,
            &json!({"manifest_key": "wrong-manifest", "accepted": true}),
        )?;
        Ok(())
    });

    let status = daemon.dispatch(&Request {
        op: "sandbox.plugin.status".to_owned(),
        invocation_id: "plugin-status-health-fail".to_owned(),
        args: json!({"probe_services": true, "probe_timeout_ms": 1000}),
    });
    assert_eq!(status["success"], true);
    assert_eq!(status["service_health"][0]["success"], false);
    assert!(
        value_str(
            &status["service_health"][0]["error"],
            "probe error must be a string"
        )?
        .contains(&manifest_key),
        "status response: {status:?}"
    );
    assert_eq!(status["connected_ppc_routes"], json!([]));
    assert_eq!(status["connected_ppc_services"], json!([]));
    assert_eq!(
        status["loaded_plugins"][0]["services"][0]["state"],
        "stopped"
    );
    {
        let state = daemon.plugin().lock_state()?;
        assert!(
            !state.service_snapshots.contains_key(&service_instance_id),
            "failed health probe should release retained snapshot"
        );
        drop(state);
    }
    join_test_thread(server, "server thread panicked")?;
    remove_test_tree(&layer_stack_root)?;
    Ok(())
}
