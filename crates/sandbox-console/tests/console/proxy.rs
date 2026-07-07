use http::{Method, Request, StatusCode};
use hyper_util::rt::TokioIo;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

use crate::support;

#[tokio::test]
async fn shared_route_swaps_prefix_and_preserves_everything() {
    let daemon = support::FakeDaemonHttp::spawn().await;
    let daemon_addr = daemon.addr;
    let gateway =
        support::FakeGateway::spawn(move |_| vec![support::record_line("eos-1", daemon_addr)])
            .await;
    let console = support::spawn_console_default(gateway.addr).await;

    let request = Request::builder()
        .method(Method::GET)
        .uri("/s/eos-1/shared/5173/assets/app.js?v=1&x=2")
        .header(http::header::HOST, "console.test")
        .header("x-test-header", "keep-me")
        .body(support::empty_body())
        .expect("build proxy request");
    let (response, _sender) = support::send_request(console, request).await;

    assert_eq!(response.status(), StatusCode::OK);
    let echo = support::body_json(response).await;
    assert_eq!(echo["method"], "GET");
    assert_eq!(echo["target"], "/forward/shared/5173/assets/app.js?v=1&x=2");
    assert_eq!(echo["headers"]["x-test-header"], "keep-me");
    assert_eq!(echo["headers"]["host"], "console.test");
    assert_eq!(echo["headers"]["x-forwarded-for"], "127.0.0.1");
}

#[tokio::test]
async fn isolated_route_and_post_body_pass_through() {
    let daemon = support::FakeDaemonHttp::spawn().await;
    let daemon_addr = daemon.addr;
    let gateway =
        support::FakeGateway::spawn(move |_| vec![support::record_line("eos-1", daemon_addr)])
            .await;
    let console = support::spawn_console_default(gateway.addr).await;

    let request = Request::builder()
        .method(Method::POST)
        .uri("/s/eos-1/isolated=ws-7/3000/submit")
        .header(http::header::HOST, "console.test")
        .body(support::full_body("payload-bytes"))
        .expect("build proxy request");
    let (response, _sender) = support::send_request(console, request).await;

    assert_eq!(response.status(), StatusCode::OK);
    let echo = support::body_json(response).await;
    assert_eq!(echo["method"], "POST");
    assert_eq!(echo["target"], "/forward/isolated=ws-7/3000/submit");
    assert_eq!(echo["body"], "payload-bytes");
}

#[tokio::test]
async fn invalid_routes_are_400_without_gateway_calls() {
    let gateway = support::FakeGateway::spawn(|_| Vec::new()).await;
    let console = support::spawn_console_default(gateway.addr).await;

    for path in [
        "/s/eos-1",
        "/s/eos-1/",
        "/s/eos-1/bogus/5173/",
        "/s/eos-1/shared/notaport/",
        "/s/eos-1/shared/0/",
        "/s/eos-1/shared/70000/",
        "/s/eos-1/isolated=/3000/",
        "/s//shared/5173/",
    ] {
        let response = support::get(console, path).await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "path: {path}");
    }
    assert_eq!(gateway.request_count(), 0);
}

#[tokio::test]
async fn unknown_sandbox_is_404_and_missing_endpoint_is_503() {
    let gateway = support::FakeGateway::spawn(|request| {
        let sandbox_id = request["args"]["sandbox_id"].as_str().unwrap_or_default();
        if sandbox_id == "eos-gone" {
            vec![support::not_found_line("eos-gone")]
        } else {
            vec![support::no_endpoint_line(sandbox_id)]
        }
    })
    .await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::get(console, "/s/eos-gone/shared/5173/").await;
    assert_eq!(response.status(), StatusCode::NOT_FOUND);

    let response = support::get(console, "/s/eos-new/shared/5173/").await;
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
}

#[tokio::test]
async fn dead_daemon_is_502() {
    let closed = support::closed_port().await;
    let gateway =
        support::FakeGateway::spawn(move |_| vec![support::record_line("eos-1", closed)]).await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::get(console, "/s/eos-1/shared/5173/").await;
    assert_eq!(response.status(), StatusCode::BAD_GATEWAY);
}

#[tokio::test]
async fn daemon_http_errors_pass_through_verbatim() {
    let daemon = support::FakeDaemonHttp::spawn().await;
    let daemon_addr = daemon.addr;
    let gateway =
        support::FakeGateway::spawn(move |_| vec![support::record_line("eos-1", daemon_addr)])
            .await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::get(console, "/s/eos-1/shared/5173/daemon-error").await;
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let text = support::body_text(response).await;
    assert_eq!(text, "isolated workspace has no reachable IP");
}

#[tokio::test]
async fn endpoint_resolution_is_cached_across_requests() {
    let daemon = support::FakeDaemonHttp::spawn().await;
    let daemon_addr = daemon.addr;
    let gateway =
        support::FakeGateway::spawn(move |_| vec![support::record_line("eos-1", daemon_addr)])
            .await;
    let console = support::spawn_console_default(gateway.addr).await;

    for _ in 0..4 {
        let response = support::get(console, "/s/eos-1/shared/5173/").await;
        assert_eq!(response.status(), StatusCode::OK);
    }

    assert_eq!(
        gateway.request_count(),
        1,
        "asset bursts resolve the record once"
    );
}

#[tokio::test]
async fn websocket_upgrade_tunnels_bytes_both_ways() {
    let daemon = support::FakeDaemonHttp::spawn().await;
    let daemon_addr = daemon.addr;
    let gateway =
        support::FakeGateway::spawn(move |_| vec![support::record_line("eos-1", daemon_addr)])
            .await;
    let console = support::spawn_console_default(gateway.addr).await;

    let request = Request::builder()
        .method(Method::GET)
        .uri("/s/eos-1/shared/5173/ws")
        .header(http::header::HOST, "console.test")
        .header(http::header::CONNECTION, "upgrade")
        .header(http::header::UPGRADE, "echo")
        .body(support::empty_body())
        .expect("build upgrade request");
    let (mut response, _sender) = support::send_request(console, request).await;

    assert_eq!(response.status(), StatusCode::SWITCHING_PROTOCOLS);
    let upgraded = hyper::upgrade::on(&mut response)
        .await
        .expect("client upgrade");
    let mut upgraded = TokioIo::new(upgraded);
    upgraded
        .write_all(b"ping-through-tunnel")
        .await
        .expect("write upgraded bytes");
    let mut echo = [0u8; 19];
    upgraded
        .read_exact(&mut echo)
        .await
        .expect("read upgraded echo");
    assert_eq!(&echo, b"ping-through-tunnel");
}
