use http::{Method, Request, StatusCode};
use serde_json::json;

use crate::support;

#[tokio::test]
async fn file_route_posts_to_daemon_http() {
    let daemon = support::FakeDaemonHttp::spawn().await;
    let daemon_addr = daemon.addr;
    let gateway =
        support::FakeGateway::spawn(move |_| vec![support::record_line("eos-1", daemon_addr)])
            .await;
    let console = support::spawn_console_default(gateway.addr).await;

    let request = Request::builder()
        .method(Method::POST)
        .uri("/api/sandboxes/eos-1/files/list")
        .header(http::header::HOST, "console.test")
        .header(http::header::CONTENT_TYPE, "application/json")
        .body(support::full_body(json!({"path": "src"}).to_string()))
        .expect("build daemon api request");
    let (response, _sender) = support::send_request(console, request).await;

    assert_eq!(response.status(), StatusCode::OK);
    let echo = support::body_json(response).await;
    assert_eq!(echo["method"], "POST");
    assert_eq!(echo["target"], "/files/list");
    assert_eq!(echo["body"], json!({"path": "src"}).to_string());
}

#[tokio::test]
async fn observability_route_posts_to_daemon_http() {
    let daemon = support::FakeDaemonHttp::spawn().await;
    let daemon_addr = daemon.addr;
    let gateway =
        support::FakeGateway::spawn(move |_| vec![support::record_line("eos-1", daemon_addr)])
            .await;
    let console = support::spawn_console_default(gateway.addr).await;

    let request = Request::builder()
        .method(Method::POST)
        .uri("/api/sandboxes/eos-1/observability/cgroup")
        .header(http::header::HOST, "console.test")
        .header(http::header::CONTENT_TYPE, "application/json")
        .body(support::full_body(
            json!({"scope": "sandbox", "window_ms": 30000}).to_string(),
        ))
        .expect("build daemon api request");
    let (response, _sender) = support::send_request(console, request).await;

    assert_eq!(response.status(), StatusCode::OK);
    let echo = support::body_json(response).await;
    assert_eq!(echo["method"], "POST");
    assert_eq!(echo["target"], "/observability/cgroup");
    assert_eq!(
        echo["body"],
        json!({"scope": "sandbox", "window_ms": 30000}).to_string()
    );
}
