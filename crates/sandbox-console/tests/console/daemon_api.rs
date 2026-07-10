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
async fn files_list_rejects_other_methods_before_endpoint_lookup() {
    let gateway = support::FakeGateway::spawn(|_| Vec::new()).await;
    let console = support::spawn_console_default(gateway.addr).await;

    let request = Request::builder()
        .method(Method::GET)
        .uri("/api/sandboxes/eos-1/files/list")
        .header(http::header::HOST, "console.test")
        .body(support::empty_body())
        .expect("build wrong-method list request");
    let (response, _sender) = support::send_request(console, request).await;

    assert_eq!(response.status(), StatusCode::METHOD_NOT_ALLOWED);
    assert_eq!(gateway.request_count(), 0);
}

#[tokio::test]
async fn removed_daemon_operation_routes_are_not_found() {
    let gateway = support::FakeGateway::spawn(|_| Vec::new()).await;
    let console = support::spawn_console_default(gateway.addr).await;
    let removed = [
        "/api/sandboxes/eos-1/files/read",
        "/api/sandboxes/eos-1/files/write",
        "/api/sandboxes/eos-1/files/edit",
        "/api/sandboxes/eos-1/files/blame",
        "/api/sandboxes/eos-1/files/list/extra",
        "/api/sandboxes/eos/nested/files/list",
        "/api/sandboxes/eos-1/observability/snapshot",
        "/api/sandboxes/eos-1/observability/trace",
        "/api/sandboxes/eos-1/observability/events",
        "/api/sandboxes/eos-1/observability/cgroup",
        "/api/sandboxes/eos-1/observability/layerstack",
    ];

    for path in removed {
        let request = Request::builder()
            .method(Method::POST)
            .uri(path)
            .header(http::header::HOST, "console.test")
            .header(http::header::CONTENT_TYPE, "application/json")
            .body(support::full_body("{}"))
            .expect("build removed-route request");
        let (response, _sender) = support::send_request(console, request).await;
        assert_eq!(
            response.status(),
            StatusCode::NOT_FOUND,
            "removed route must be absent: {path}"
        );
    }

    assert_eq!(
        gateway.request_count(),
        0,
        "rejected routes must not resolve daemon endpoints"
    );
}
