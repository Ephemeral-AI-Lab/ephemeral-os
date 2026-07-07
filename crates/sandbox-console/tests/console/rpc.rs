use std::time::Duration;

use http::StatusCode;
use sandbox_protocol::GATEWAY_AUTH_FIELD;
use serde_json::json;

use crate::support;

fn rpc_body(op: &str) -> serde_json::Value {
    json!({
        "op": op,
        "scope": {"kind": "system"},
        "args": {},
    })
}

#[tokio::test]
async fn one_shot_passes_result_through_verbatim() {
    let gateway =
        support::FakeGateway::spawn(|_| vec![json!({"sandboxes": [{"id": "eos-1"}]}).to_string()])
            .await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::post_rpc(console, &rpc_body("list_sandboxes")).await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = support::body_json(response).await;
    assert_eq!(body, json!({"sandboxes": [{"id": "eos-1"}]}));

    let seen = gateway.requests();
    assert_eq!(seen.len(), 1);
    assert_eq!(seen[0]["op"], "list_sandboxes");
    assert_eq!(seen[0]["scope"], json!({"kind": "system"}));
    assert_eq!(seen[0][GATEWAY_AUTH_FIELD], support::TEST_AUTH_TOKEN);
    assert_eq!(seen[0]["_stream_logs"], json!(false));
    assert!(
        seen[0]["request_id"]
            .as_str()
            .is_some_and(|id| !id.is_empty()),
        "console must inject a request_id"
    );
}

#[tokio::test]
async fn protocol_error_returns_in_body_with_http_200() {
    let gateway = support::FakeGateway::spawn(|_| {
        vec![json!({
            "error": {"kind": "invalid_request", "message": "boom", "details": {}}
        })
        .to_string()]
    })
    .await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::post_rpc(console, &rpc_body("inspect_sandbox")).await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = support::body_json(response).await;
    assert_eq!(body["error"]["kind"], "invalid_request");
    assert_eq!(body["error"]["message"], "boom");
}

#[tokio::test]
async fn sandbox_scope_and_client_request_id_pass_through() {
    let gateway = support::FakeGateway::spawn(|_| vec![json!({"ok": true}).to_string()]).await;
    let console = support::spawn_console_default(gateway.addr).await;

    let body = json!({
        "op": "exec_command",
        "request_id": "req-fixed-1",
        "scope": {"kind": "sandbox", "sandbox_id": "eos-abc"},
        "args": {"cmd": "pwd", "yield_time_ms": 0},
    });
    let response = support::post_rpc(console, &body).await;

    assert_eq!(response.status(), StatusCode::OK);
    let seen = gateway.requests();
    assert_eq!(seen[0]["request_id"], "req-fixed-1");
    assert_eq!(
        seen[0]["scope"],
        json!({"kind": "sandbox", "sandbox_id": "eos-abc"})
    );
    assert_eq!(seen[0]["args"], json!({"cmd": "pwd", "yield_time_ms": 0}));
}

#[tokio::test]
async fn malformed_body_is_400() {
    let gateway = support::FakeGateway::spawn(|_| Vec::new()).await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::post_rpc_raw(console, "this is not json", None).await;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = support::body_json(response).await;
    assert_eq!(body["error"]["kind"], "bad_json");

    let response = support::post_rpc(console, &json!({"scope": {"kind": "system"}})).await;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = support::body_json(response).await;
    assert_eq!(body["error"]["kind"], "invalid_request");

    assert_eq!(
        gateway.request_count(),
        0,
        "bad bodies never reach the gateway"
    );
}

#[tokio::test]
async fn gateway_unreachable_is_502() {
    let closed = support::closed_port().await;
    let console = support::spawn_console_default(closed).await;

    let response = support::post_rpc(console, &rpc_body("list_sandboxes")).await;

    assert_eq!(response.status(), StatusCode::BAD_GATEWAY);
    let body = support::body_json(response).await;
    assert_eq!(body["error"]["kind"], "connection_error");
}

#[tokio::test]
async fn gateway_timeout_is_504() {
    let gateway = support::FakeGateway::spawn_silent().await;
    let console = support::spawn_console(gateway.addr, None, Duration::from_millis(200)).await;

    let response = support::post_rpc(console, &rpc_body("list_sandboxes")).await;

    assert_eq!(response.status(), StatusCode::GATEWAY_TIMEOUT);
    let body = support::body_json(response).await;
    assert_eq!(body["error"]["kind"], "gateway_timeout");
}

#[tokio::test]
async fn sse_variant_streams_logs_then_result() {
    let gateway = support::FakeGateway::spawn(|_| {
        vec![
            "cli_log(\"creating sandbox eos-1...\")".to_owned(),
            "cli_log(\"daemon ready\")".to_owned(),
            json!({"id": "eos-1", "state": "ready"}).to_string(),
        ]
    })
    .await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::post_rpc_sse(console, &rpc_body("create_sandbox")).await;

    assert_eq!(response.status(), StatusCode::OK);
    let content_type = response
        .headers()
        .get(http::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .to_owned();
    assert!(content_type.starts_with("text/event-stream"));

    let text = support::body_text(response).await;
    let events: Vec<&str> = text.split("\n\n").filter(|part| !part.is_empty()).collect();
    assert_eq!(events.len(), 3, "two logs plus one result: {text}");
    assert_eq!(
        events[0],
        "event: log\ndata: {\"line\":\"creating sandbox eos-1...\"}"
    );
    assert_eq!(events[1], "event: log\ndata: {\"line\":\"daemon ready\"}");
    assert!(events[2].starts_with("event: result\ndata: "));

    let seen = gateway.requests();
    assert_eq!(seen[0]["_stream_logs"], json!(true));
}

#[tokio::test]
async fn sse_transport_failure_emits_error_event() {
    let gateway = support::FakeGateway::spawn(|_| vec!["cli_log(\"starting\")".to_owned()]).await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::post_rpc_sse(console, &rpc_body("create_sandbox")).await;
    let text = support::body_text(response).await;

    assert!(text.contains("event: log"));
    assert!(
        text.contains("event: error"),
        "stream ends in an error event: {text}"
    );
}
