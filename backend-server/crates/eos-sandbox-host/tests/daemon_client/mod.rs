#![allow(clippy::unwrap_used)]

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use eos_types::{JsonObject, SandboxId};
use serde_json::Value;

use super::codec::{can_retry_empty_response, decode_and_classify};
use super::tcp::{authenticated_envelope_json, io_failed};
use super::*;
use crate::provider::RawExecResult;
use crate::support::MockAdapter;

fn sid() -> SandboxId {
    "sb-1".parse().unwrap()
}

fn ok_result(stdout: &str) -> RawExecResult {
    RawExecResult {
        exit_code: 0,
        stdout: stdout.to_owned(),
        stderr: String::new(),
        success: true,
    }
}

fn connect_failed() -> RawExecResult {
    io_failed(
        THIN_CLIENT_CONNECT_FAILED,
        "EOS_DAEMON_CONNECT_FAILED:x".to_owned(),
    )
}

fn empty_response() -> RawExecResult {
    io_failed(THIN_CLIENT_IO_FAILED, EMPTY_RESPONSE_MESSAGE.to_owned())
}

/// Extract the daemon envelope JSON embedded in a thin-client shell command.
fn envelope_from_command(cmd: &str) -> Value {
    let start = cmd.find('{').expect("envelope start");
    let end = cmd.rfind('}').expect("envelope end");
    serde_json::from_str(&cmd[start..=end]).expect("envelope parses")
}

fn client_with(adapter: MockAdapter) -> (DaemonClient, Arc<std::sync::Mutex<Vec<String>>>) {
    let calls = adapter.call_log();
    let registry = ProviderRegistry::new();
    registry.set_default(Arc::new(adapter));
    (DaemonClient::new(Arc::new(registry)), calls)
}

// AC-03: envelope {op, invocation_id, args.layer_stack_root}; cancel mints a
// fresh id; the auth field is added only on the token TCP path.
#[tokio::test]
async fn envelope_shape_and_auth() {
    let (client, calls) =
        client_with(MockAdapter::new().with_exec(|_cmd| ok_result("{\"ok\":true}")));
    client
        .call_daemon_api(
            &sid(),
            "api.v1.read_file",
            JsonObject::new(),
            60,
            "/eos/state/layer-stack",
        )
        .await
        .unwrap();
    let cmd = calls.lock().unwrap()[0].clone();
    let env = envelope_from_command(&cmd);
    assert_eq!(env["op"], serde_json::json!("api.v1.read_file"));
    assert_eq!(
        env["args"]["layer_stack_root"],
        serde_json::json!("/eos/state/layer-stack")
    );
    let inv = env["invocation_id"].as_str().unwrap();
    assert_eq!(inv.len(), 32, "uuid4().hex is 32 hex chars (no dashes)");
    assert!(inv.bytes().all(|b| b.is_ascii_hexdigit()));

    // cancel mints a fresh top-level invocation id.
    let (client, calls) =
        client_with(MockAdapter::new().with_exec(|_cmd| ok_result("{\"ok\":true}")));
    client
        .call_daemon_api(
            &sid(),
            "api.v1.cancel",
            JsonObject::new(),
            15,
            "/eos/state/layer-stack",
        )
        .await
        .unwrap();
    let env = envelope_from_command(&calls.lock().unwrap()[0]);
    assert_eq!(env["op"], serde_json::json!("api.v1.cancel"));
    assert_eq!(env["invocation_id"].as_str().unwrap().len(), 32);

    // auth field added only with a token.
    let endpoint = DaemonTcpEndpoint {
        host: "127.0.0.1".to_owned(),
        port: 49153,
        internal_port: Some(37657),
        auth_token: "tok".to_owned(),
    };
    let authed = authenticated_envelope_json("{\"op\":\"x\"}", &endpoint);
    let parsed: Value = serde_json::from_str(&authed).unwrap();
    assert_eq!(parsed[DAEMON_AUTH_FIELD], serde_json::json!("tok"));
    let no_token = DaemonTcpEndpoint {
        auth_token: String::new(),
        ..endpoint
    };
    assert_eq!(
        authenticated_envelope_json("{\"op\":\"x\"}", &no_token),
        "{\"op\":\"x\"}"
    );
}

// AC-04: CONNECT_FAILED triggers spawn -> readiness -> replay; a mutating op
// returning empty-response fails closed (no spawn).
#[tokio::test]
async fn recovery_retry_and_fail_closed() {
    // Case 1: connect-failed then recovery.
    let original_calls = Arc::new(AtomicUsize::new(0));
    let counter = Arc::clone(&original_calls);
    let (client, calls) = client_with(MockAdapter::new().with_exec(move |cmd| {
        if cmd.contains("--spawn") {
            return ok_result("");
        }
        if cmd.contains("api.runtime.ready") {
            return ok_result("{\"ready\":true}");
        }
        if counter.fetch_add(1, Ordering::SeqCst) == 0 {
            connect_failed()
        } else {
            ok_result("{\"replayed\":true}")
        }
    }));
    let response = client
        .call_daemon_api(
            &sid(),
            "api.v1.read_file",
            JsonObject::new(),
            60,
            "/eos/state/layer-stack",
        )
        .await
        .unwrap();
    assert_eq!(response["replayed"], serde_json::json!(true));
    let log = calls.lock().unwrap().clone();
    assert!(log.iter().any(|c| c.contains("--spawn")), "spawn must run");
    assert!(
        log.iter().any(|c| c.contains("api.runtime.ready")),
        "readiness probe must run"
    );

    // Case 2: empty-response on a mutating op fails closed (no spawn).
    let (client, calls) = client_with(MockAdapter::new().with_exec(|cmd| {
        if cmd.contains("--spawn") {
            return ok_result(""); // would mean recovery: must not happen
        }
        empty_response()
    }));
    let err = client
        .call_daemon_api(
            &sid(),
            "api.v1.write_file",
            JsonObject::new(),
            60,
            "/eos/state/layer-stack",
        )
        .await
        .unwrap_err();
    assert!(matches!(
        err,
        SandboxHostError::ExecFailed { exit_code: 98, .. }
    ));
    assert!(
        !calls.lock().unwrap().iter().any(|c| c.contains("--spawn")),
        "fail-closed: mutating op must not spawn/replay"
    );
}

#[tokio::test]
async fn write_stdin_empty_response_fails_closed_without_replay() {
    let (client, calls) = client_with(MockAdapter::new().with_exec(|_cmd| empty_response()));
    let mut args = JsonObject::new();
    args.insert(
        "command_session_id".to_owned(),
        Value::String("cmd_1".to_owned()),
    );
    args.insert("chars".to_owned(), Value::String("payload".to_owned()));

    let err = client
        .call_daemon_api(
            &sid(),
            "api.v1.write_stdin",
            args,
            60,
            "/eos/state/layer-stack",
        )
        .await
        .unwrap_err();

    assert!(matches!(
        err,
        SandboxHostError::ExecFailed { exit_code: 98, .. }
    ));
    let log = calls.lock().unwrap().clone();
    assert_eq!(
        log.len(),
        1,
        "write_stdin empty response must not spawn or replay"
    );
    assert!(!log[0].contains("--spawn"));
    assert!(!log[0].contains("api.runtime.ready"));

    let env = envelope_from_command(&log[0]);
    assert_eq!(env["op"], serde_json::json!("api.v1.write_stdin"));
    assert_eq!(
        env["args"]["command_session_id"],
        serde_json::json!("cmd_1")
    );
    assert_eq!(env["args"]["chars"], serde_json::json!("payload"));
}

// AC-05: a non-policy daemon error decodes to DaemonDispatch; a handler-level
// policy result (success=false + non-empty status) is returned, not raised.
#[test]
fn decode_error_vs_policy_result() {
    let dispatch = decode_and_classify(&ok_result(
        "{\"error\":{\"kind\":\"WorkspaceBindingError\",\"message\":\"boom\"}}",
    ))
    .unwrap_err();
    assert!(matches!(
        dispatch,
        SandboxHostError::DaemonDispatch { kind, message, .. }
            if kind == "WorkspaceBindingError" && message == "boom"
    ));

    let policy = decode_and_classify(&ok_result(
        "{\"success\":false,\"status\":\"rejected\",\"error\":{\"reason\":\"conflict\"}}",
    ))
    .unwrap();
    assert_eq!(policy["status"], serde_json::json!("rejected"));
    assert_eq!(policy["error"]["reason"], serde_json::json!("conflict"));

    // a non-object error string still raises DaemonDispatch.
    let stringy = decode_and_classify(&ok_result("{\"error\":\"down\"}")).unwrap_err();
    assert!(matches!(
        stringy,
        SandboxHostError::DaemonDispatch { message, .. } if message == "down"
    ));
}

// AC-07b: concurrent resolves single-flight (one adapter resolve), no guard
// held across the await, and cache invalidation triggers a fresh resolve.
#[tokio::test]
async fn tcp_endpoint_singleflight_lock_order() {
    let endpoint = DaemonTcpEndpoint {
        host: "127.0.0.1".to_owned(),
        port: 49153,
        internal_port: Some(37657),
        auth_token: String::new(),
    };
    let adapter = MockAdapter::new().with_tcp(endpoint).with_tcp_delay_ms(25);
    let resolves = adapter.tcp_resolve_counter();
    let registry = ProviderRegistry::new();
    let adapter_arc: Arc<dyn ProviderAdapter> = Arc::new(adapter);
    registry.set_default(Arc::clone(&adapter_arc));
    let client = DaemonClient::new(Arc::new(registry));

    let id = sid();
    // Two concurrent callers share ONE async resolve (single-flight).
    let (a, b) = tokio::join!(
        client.resolve_daemon_tcp_endpoint(&*adapter_arc, &id),
        client.resolve_daemon_tcp_endpoint(&*adapter_arc, &id),
    );
    assert!(a.is_some() && b.is_some());
    assert_eq!(a.unwrap().port, 49153);
    assert_eq!(
        resolves.load(Ordering::SeqCst),
        1,
        "single-flight: one resolve"
    );

    // A third call hits the cache (no new resolve).
    let _ = client.resolve_daemon_tcp_endpoint(&*adapter_arc, &id).await;
    assert_eq!(
        resolves.load(Ordering::SeqCst),
        1,
        "cache hit: no new resolve"
    );

    // Invalidation forces a fresh single-flight resolve.
    client.invalidate_daemon_tcp_endpoint(&id);
    let _ = client.resolve_daemon_tcp_endpoint(&*adapter_arc, &id).await;
    assert_eq!(
        resolves.load(Ordering::SeqCst),
        2,
        "re-resolve after invalidation"
    );
}

#[test]
fn empty_response_gating_matches_python_set() {
    for op in [
        "api.edit_file",
        "api.v1.edit_file",
        "api.write_file",
        "api.v1.write_file",
        "api.v1.exec_command",
        "api.v1.write_stdin",
        "plugin.install",
    ] {
        assert!(!can_retry_empty_response(op), "{op} must fail closed");
    }
    for op in [
        "api.v1.read_file",
        "api.runtime.ready",
        "api.ensure_workspace_base",
        "api.v1.cancel",
        "api.shell", // non-v1 shell is NOT in the literal fail-closed set
    ] {
        assert!(can_retry_empty_response(op), "{op} must be retryable");
    }
}
