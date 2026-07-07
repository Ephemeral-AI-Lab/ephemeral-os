use http::StatusCode;

use crate::support;

#[tokio::test]
async fn healthy_daemon_answers_ok() {
    let daemon = support::FakeDaemonHttp::spawn().await;
    let daemon_addr = daemon.addr;
    let gateway =
        support::FakeGateway::spawn(move |_| vec![support::record_line("eos-1", daemon_addr)])
            .await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::get(console, "/api/sandboxes/eos-1/health").await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = support::body_json(response).await;
    assert_eq!(body["status"], "ok");
}

#[tokio::test]
async fn dead_daemon_answers_unreachable() {
    let closed = support::closed_port().await;
    let gateway =
        support::FakeGateway::spawn(move |_| vec![support::record_line("eos-1", closed)]).await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::get(console, "/api/sandboxes/eos-1/health").await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = support::body_json(response).await;
    assert_eq!(body["status"], "unreachable");
    assert!(body["detail"]
        .as_str()
        .is_some_and(|detail| !detail.is_empty()));
}

#[tokio::test]
async fn unknown_sandbox_is_404() {
    let gateway =
        support::FakeGateway::spawn(|_| vec![support::not_found_line("eos-missing")]).await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::get(console, "/api/sandboxes/eos-missing/health").await;

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = support::body_json(response).await;
    assert_eq!(body["error"]["kind"], "unknown_sandbox");
}

#[tokio::test]
async fn sandbox_without_endpoint_is_503() {
    let gateway = support::FakeGateway::spawn(|_| vec![support::no_endpoint_line("eos-new")]).await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::get(console, "/api/sandboxes/eos-new/health").await;

    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body = support::body_json(response).await;
    assert_eq!(body["error"]["kind"], "sandbox_not_ready");
}
