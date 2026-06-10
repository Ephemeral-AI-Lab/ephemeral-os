use super::super::*;
use super::support::*;

use crate::dispatcher::OpTable;
use crate::wire::Request;
use eos_plugin::{PpcDirection, PpcEnvelope};
use serde_json::{json, Value};
use std::io::Write;
use std::sync::{mpsc, Arc};
use std::time::Duration;

#[test]
fn connected_read_only_plugin_op_round_trips_over_ppc() -> TestResult {
    let _guard = PluginTestGuard::new()?;
    let table = OpTable::with_builtins();
    let (layer_stack_root, workspace_root) = test_bound_workspace("read-only-ppc")?;
    let ensure = table.dispatch(&Request {
        op: "api.plugin.ensure".to_owned(),
        invocation_id: "plugin-ensure-test".to_owned(),
        args: json!({
            "manifest": generic_service_manifest("digest-a", "hover"),
            "layer_stack_root": layer_stack_root.to_string_lossy().into_owned(),
            "workspace_root": workspace_root.to_string_lossy().into_owned()
        }),
    });
    assert_eq!(ensure["success"], true);

    let (client_stream, mut server_stream) = ppc_stream_pair()?;
    register_ppc_client_for_tests("plugin.generic.hover", client_stream)?;
    let server = std::thread::spawn(move || -> TestResult {
        let request = read_ppc_request(&mut server_stream, "read ppc request")?;
        assert_eq!(request.message_id, "plugin-hover-test");
        assert_eq!(request.op, "plugin.generic.hover");
        assert!(request.body.contains("caller-plugin"));
        let reply = PpcEnvelope {
            message_id: request.message_id,
            direction: PpcDirection::Reply,
            op: "reply".to_owned(),
            body: r#"{"success":true,"from_ppc":true}"#.to_owned(),
        };
        server_stream.write_all(&reply.encode()?)?;
        Ok(())
    });

    let routed = table.dispatch(&Request {
        op: "plugin.generic.hover".to_owned(),
        invocation_id: "plugin-hover-test".to_owned(),
        args: json!({"caller_id": "caller-plugin"}),
    });
    assert_eq!(routed["success"], true);
    assert_eq!(routed["from_ppc"], true);
    join_test_thread(server, "server thread panicked")?;
    remove_test_tree(&layer_stack_root)?;
    Ok(())
}

#[test]
fn concurrent_read_only_plugin_ops_share_one_ppc_client() -> TestResult {
    let _guard = PluginTestGuard::new()?;
    let table = Arc::new(OpTable::with_builtins());
    let (layer_stack_root, workspace_root) = test_bound_workspace("concurrent-read-only")?;
    let ensure = table.dispatch(&Request {
        op: "api.plugin.ensure".to_owned(),
        invocation_id: "plugin-ensure-test".to_owned(),
        args: json!({
            "manifest": generic_service_manifest("digest-a", "hover"),
            "layer_stack_root": layer_stack_root.to_string_lossy().into_owned(),
            "workspace_root": workspace_root.to_string_lossy().into_owned()
        }),
    });
    assert_eq!(ensure["success"], true);

    let (client_stream, mut server_stream) = ppc_stream_pair()?;
    register_ppc_client_for_tests("plugin.generic.hover", client_stream)?;
    let (first_seen_tx, first_seen_rx) = mpsc::channel();
    let (second_seen_tx, second_seen_rx) = mpsc::channel();
    let (reply_first_tx, reply_first_rx) = mpsc::channel();
    let server = std::thread::spawn(move || -> TestResult {
        let first = read_ppc_request(&mut server_stream, "read first ppc request")?;
        first_seen_tx.send(first.message_id.clone())?;
        let second = read_ppc_request(&mut server_stream, "read second ppc request")?;
        second_seen_tx.send(second.message_id.clone())?;
        reply_first_rx.recv()?;
        write_ppc_reply_result(
            &mut server_stream,
            second.message_id,
            r#"{"success":true,"seq":2}"#,
        )?;
        write_ppc_reply_result(
            &mut server_stream,
            first.message_id,
            r#"{"success":true,"seq":1}"#,
        )?;
        Ok(())
    });

    let first_table = Arc::clone(&table);
    let first = std::thread::spawn(move || -> Result<Value, TestError> {
        Ok(first_table.dispatch(&Request {
            op: "plugin.generic.hover".to_owned(),
            invocation_id: "plugin-hover-concurrent-a".to_owned(),
            args: json!({"caller_id": "caller-plugin", "request": "a"}),
        }))
    });
    assert_eq!(
        first_seen_rx.recv_timeout(Duration::from_secs(1))?,
        "plugin-hover-concurrent-a"
    );

    let (second_started_tx, second_started_rx) = mpsc::channel();
    let second_table = Arc::clone(&table);
    let second = std::thread::spawn(move || -> Result<Value, TestError> {
        second_started_tx.send(())?;
        Ok(second_table.dispatch(&Request {
            op: "plugin.generic.hover".to_owned(),
            invocation_id: "plugin-hover-concurrent-b".to_owned(),
            args: json!({"caller_id": "caller-plugin", "request": "b"}),
        }))
    });
    second_started_rx.recv_timeout(Duration::from_secs(1))?;
    assert_eq!(
        second_seen_rx.recv_timeout(Duration::from_secs(1))?,
        "plugin-hover-concurrent-b"
    );
    reply_first_tx.send(())?;

    let first_response = join_value_thread(first, "first dispatch thread panicked")?;
    let second_response = join_value_thread(second, "second dispatch thread panicked")?;
    assert_eq!(first_response["success"], true);
    assert_eq!(first_response["seq"], 1);
    assert_eq!(second_response["success"], true);
    assert_eq!(second_response["seq"], 2);
    join_test_thread(server, "server thread panicked")?;
    remove_test_tree(&layer_stack_root)?;
    Ok(())
}

#[test]
fn concurrent_read_only_plugin_ops_match_out_of_order_replies() -> TestResult {
    let _guard = PluginTestGuard::new()?;
    let table = Arc::new(OpTable::with_builtins());
    let (layer_stack_root, workspace_root) =
        test_bound_workspace("concurrent-read-only-out-of-order")?;
    let ensure = table.dispatch(&Request {
        op: "api.plugin.ensure".to_owned(),
        invocation_id: "plugin-ensure-test".to_owned(),
        args: json!({
            "manifest": generic_service_manifest("digest-a", "hover"),
            "layer_stack_root": layer_stack_root.to_string_lossy().into_owned(),
            "workspace_root": workspace_root.to_string_lossy().into_owned()
        }),
    });
    assert_eq!(ensure["success"], true);

    let (client_stream, mut server_stream) = ppc_stream_pair()?;
    register_ppc_client_for_tests("plugin.generic.hover", client_stream)?;
    let (both_seen_tx, both_seen_rx) = mpsc::channel();
    let server = std::thread::spawn(move || -> TestResult {
        let first = read_ppc_request(&mut server_stream, "read first ppc request")?;
        let second = read_ppc_request(&mut server_stream, "read second ppc request")?;
        let mut message_ids = vec![first.message_id.clone(), second.message_id.clone()];
        message_ids.sort();
        both_seen_tx.send(message_ids)?;
        let request_a = "plugin-hover-concurrent-a";
        let request_b = "plugin-hover-concurrent-b";
        let reply_a = if first.message_id == request_a {
            first.message_id.clone()
        } else if second.message_id == request_a {
            second.message_id.clone()
        } else {
            return Err("missing concurrent request a".into());
        };
        let reply_b = if first.message_id == request_b {
            first.message_id.clone()
        } else if second.message_id == request_b {
            second.message_id.clone()
        } else {
            return Err("missing concurrent request b".into());
        };
        write_ppc_reply_result(&mut server_stream, reply_b, r#"{"success":true,"seq":2}"#)?;
        write_ppc_reply_result(&mut server_stream, reply_a, r#"{"success":true,"seq":1}"#)?;
        Ok(())
    });

    let first_table = Arc::clone(&table);
    let first = std::thread::spawn(move || -> Result<Value, TestError> {
        Ok(first_table.dispatch(&Request {
            op: "plugin.generic.hover".to_owned(),
            invocation_id: "plugin-hover-concurrent-a".to_owned(),
            args: json!({"caller_id": "caller-plugin", "request": "a"}),
        }))
    });
    let second_table = Arc::clone(&table);
    let second = std::thread::spawn(move || -> Result<Value, TestError> {
        Ok(second_table.dispatch(&Request {
            op: "plugin.generic.hover".to_owned(),
            invocation_id: "plugin-hover-concurrent-b".to_owned(),
            args: json!({"caller_id": "caller-plugin", "request": "b"}),
        }))
    });

    let seen = both_seen_rx.recv_timeout(Duration::from_secs(1))?;
    assert_eq!(
        seen,
        vec![
            "plugin-hover-concurrent-a".to_owned(),
            "plugin-hover-concurrent-b".to_owned()
        ]
    );
    let first_response = join_value_thread(first, "first dispatch thread panicked")?;
    let second_response = join_value_thread(second, "second dispatch thread panicked")?;
    assert_eq!(first_response["success"], true);
    assert_eq!(first_response["seq"], 1);
    assert_eq!(second_response["success"], true);
    assert_eq!(second_response["seq"], 2);
    join_test_thread(server, "server thread panicked")?;
    remove_test_tree(&layer_stack_root)?;
    Ok(())
}

#[test]
fn read_only_ppc_failure_drops_connected_route() -> TestResult {
    let _guard = PluginTestGuard::new()?;
    let table = OpTable::with_builtins();
    let (layer_stack_root, workspace_root) = test_bound_workspace("read-only-broken-ppc")?;
    let ensure = table.dispatch(&Request {
        op: "api.plugin.ensure".to_owned(),
        invocation_id: "plugin-ensure-test".to_owned(),
        args: json!({
            "manifest": generic_service_manifest("digest-a", "hover"),
            "layer_stack_root": layer_stack_root.to_string_lossy().into_owned(),
            "workspace_root": workspace_root.to_string_lossy().into_owned()
        }),
    });
    assert_eq!(ensure["success"], true);

    let (client_stream, server_stream) = ppc_stream_pair()?;
    register_ppc_client_for_tests("plugin.generic.hover", client_stream)?;
    drop(server_stream);

    let routed = table.dispatch(&Request {
        op: "plugin.generic.hover".to_owned(),
        invocation_id: "plugin-hover-broken-ppc".to_owned(),
        args: json!({"caller_id": "caller-plugin"}),
    });
    assert_eq!(routed["error"]["kind"], "internal_error");

    let status = table.dispatch(&Request {
        op: "api.plugin.status".to_owned(),
        invocation_id: "plugin-status-after-broken-ppc".to_owned(),
        args: json!({}),
    });
    assert_eq!(status["connected_ppc_routes"], json!([]));
    assert_eq!(status["connected_ppc_services"], json!([]));
    remove_test_tree(&layer_stack_root)?;
    Ok(())
}

#[test]
fn read_only_service_recovers_on_next_dispatch_after_ppc_failure() -> TestResult {
    let _guard = PluginTestGuard::new()?;
    let table = OpTable::with_builtins();
    let socket_root = test_socket_root("recover-after-ppc-failure");
    let (layer_stack_root, workspace_root) = test_bound_workspace("recover-after-ppc-failure")?;
    let command = vec![
        "/bin/sh",
        "-c",
        "test \"$EOS_PLUGIN_SERVICE_ID\" = worker && sleep 30",
    ];
    let ensure = table.dispatch(&Request {
        op: "api.plugin.ensure".to_owned(),
        invocation_id: "plugin-ensure-recover-after-ppc-failure".to_owned(),
        args: json!({
            "manifest": generic_service_manifest_with_command("digest-a", "hover", command),
            "layer_stack_root": layer_stack_root.to_string_lossy().into_owned(),
            "workspace_root": workspace_root.to_string_lossy().into_owned(),
            "ppc_socket_root": socket_root.to_string_lossy().into_owned()
        }),
    });
    assert_eq!(ensure["success"], true);

    let (client_stream, server_stream) = ppc_stream_pair()?;
    register_ppc_client_for_tests("plugin.generic.hover", client_stream)?;
    attach_service_snapshot_for_tests("plugin.generic.hover")?;
    drop(server_stream);

    let failed = table.dispatch(&Request {
        op: "plugin.generic.hover".to_owned(),
        invocation_id: "plugin-hover-broken-before-recovery".to_owned(),
        args: json!({"caller_id": "caller-plugin"}),
    });
    assert_eq!(failed["error"]["kind"], "internal_error");

    let after_failure = table.dispatch(&Request {
        op: "api.plugin.status".to_owned(),
        invocation_id: "plugin-status-after-recoverable-failure".to_owned(),
        args: json!({}),
    });
    assert_eq!(after_failure["connected_ppc_routes"], json!([]));
    assert_eq!(
        after_failure["loaded_plugins"][0]["services"][0]["state"],
        "stopped"
    );

    let connector = spawn_replying_connector(
        socket_root.clone(),
        r#"{"success":true,"from_recovered_service":true}"#,
    );
    let recovered = table.dispatch(&Request {
        op: "plugin.generic.hover".to_owned(),
        invocation_id: "plugin-hover-after-recovery".to_owned(),
        args: json!({"caller_id": "caller-plugin"}),
    });
    assert_eq!(
        recovered["success"], true,
        "recovered response: {recovered:?}"
    );
    assert_eq!(recovered["from_recovered_service"], true);

    let status = table.dispatch(&Request {
        op: "api.plugin.status".to_owned(),
        invocation_id: "plugin-status-after-recovery".to_owned(),
        args: json!({}),
    });
    let service = &status["loaded_plugins"][0]["services"][0];
    assert_eq!(service["state"], "ready");
    assert_eq!(service["restart_count"], 1);
    assert_eq!(
        status["connected_ppc_routes"],
        json!(["plugin.generic.hover"])
    );

    join_test_thread(connector, "connector thread panicked")?;
    let _ = std::fs::remove_dir_all(socket_root);
    remove_test_tree(&layer_stack_root)?;
    Ok(())
}
