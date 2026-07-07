use std::path::PathBuf;
use std::time::Duration;

use http::StatusCode;

use crate::support;

fn temp_assets_dir() -> PathBuf {
    let dir = std::env::temp_dir().join(format!("console-assets-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(dir.join("assets")).expect("create asset dirs");
    std::fs::write(dir.join("index.html"), "<html>console-index</html>").expect("write index");
    std::fs::write(dir.join("assets/app.js"), "console.log('app')").expect("write app.js");
    dir
}

#[tokio::test]
async fn missing_assets_dir_serves_placeholder() {
    let gateway = support::FakeGateway::spawn(|_| Vec::new()).await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::get(console, "/").await;
    assert_eq!(response.status(), StatusCode::OK);
    let text = support::body_text(response).await;
    assert!(text.contains("SPA assets are not built"));
}

#[tokio::test]
async fn serves_files_and_falls_back_to_index_for_client_routes() {
    let gateway = support::FakeGateway::spawn(|_| Vec::new()).await;
    let assets = temp_assets_dir();
    let console =
        support::spawn_console(gateway.addr, Some(assets.clone()), Duration::from_secs(5)).await;

    let response = support::get(console, "/assets/app.js").await;
    assert_eq!(response.status(), StatusCode::OK);
    let content_type = response
        .headers()
        .get(http::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .to_owned();
    assert!(content_type.starts_with("text/javascript"));
    let text = support::body_text(response).await;
    assert_eq!(text, "console.log('app')");

    for client_route in ["/", "/sandboxes/eos-1/terminal", "/deep/link?x=1"] {
        let response = support::get(console, client_route).await;
        assert_eq!(response.status(), StatusCode::OK, "route: {client_route}");
        let text = support::body_text(response).await;
        assert!(text.contains("console-index"), "route: {client_route}");
    }

    std::fs::remove_dir_all(assets).expect("clean temp assets");
}

#[tokio::test]
async fn path_traversal_is_rejected() {
    let gateway = support::FakeGateway::spawn(|_| Vec::new()).await;
    let assets = temp_assets_dir();
    let console =
        support::spawn_console(gateway.addr, Some(assets.clone()), Duration::from_secs(5)).await;

    let response = support::get(console, "/../Cargo.toml").await;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    std::fs::remove_dir_all(assets).expect("clean temp assets");
}
