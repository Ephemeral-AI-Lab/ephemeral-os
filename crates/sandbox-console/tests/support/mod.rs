use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use bytes::Bytes;
use http::{Request, Response, StatusCode};
use http_body_util::{BodyExt as _, Full};
use hyper::body::Incoming;
use hyper::service::service_fn;
use hyper_util::rt::TokioIo;
use sandbox_console::config::ConsoleConfig;
use sandbox_console::server;
use sandbox_console::state::AppState;
use sandbox_operation_client::GatewayConfig;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};

pub const TEST_AUTH_TOKEN: &str = "test-console-token";

pub type TestBody = http_body_util::combinators::BoxBody<Bytes, hyper::Error>;

pub fn full_body(text: impl Into<String>) -> TestBody {
    Full::new(Bytes::from(text.into()))
        .map_err(|never| match never {})
        .boxed()
}

pub fn empty_body() -> TestBody {
    full_body("")
}

/// A scripted gateway: accepts JSON-line connections, records each request,
/// and answers with the handler's lines (newline appended per line).
pub struct FakeGateway {
    pub addr: SocketAddr,
    requests: Arc<Mutex<Vec<Value>>>,
}

impl FakeGateway {
    pub async fn spawn<F>(handler: F) -> Self
    where
        F: Fn(&Value) -> Vec<String> + Send + Sync + 'static,
    {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind fake gateway");
        let addr = listener.local_addr().expect("fake gateway addr");
        let requests = Arc::new(Mutex::new(Vec::new()));
        let seen = Arc::clone(&requests);
        let handler = Arc::new(handler);
        tokio::spawn(async move {
            loop {
                let Ok((stream, _)) = listener.accept().await else {
                    break;
                };
                let seen = Arc::clone(&seen);
                let handler = Arc::clone(&handler);
                tokio::spawn(async move {
                    let (read, mut write) = stream.into_split();
                    let mut line = String::new();
                    BufReader::new(read)
                        .read_line(&mut line)
                        .await
                        .expect("read gateway request line");
                    let value: Value =
                        serde_json::from_str(&line).expect("gateway request is json");
                    seen.lock().expect("requests lock").push(value.clone());
                    for out in handler(&value) {
                        let mut framed = out.into_bytes();
                        framed.push(b'\n');
                        write.write_all(&framed).await.expect("write gateway line");
                    }
                });
            }
        });
        Self { addr, requests }
    }

    /// A gateway that accepts and reads but never answers (for timeouts).
    pub async fn spawn_silent() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind silent gateway");
        let addr = listener.local_addr().expect("silent gateway addr");
        let requests = Arc::new(Mutex::new(Vec::new()));
        tokio::spawn(async move {
            loop {
                let Ok((stream, _)) = listener.accept().await else {
                    break;
                };
                tokio::spawn(async move {
                    let _held = stream;
                    tokio::time::sleep(Duration::from_secs(600)).await;
                });
            }
        });
        Self { addr, requests }
    }

    pub fn requests(&self) -> Vec<Value> {
        self.requests.lock().expect("requests lock").clone()
    }

    pub fn request_count(&self) -> usize {
        self.requests.lock().expect("requests lock").len()
    }
}

/// One JSON line for a ready sandbox record pointing `daemon_http` at `addr`.
pub fn record_line(sandbox_id: &str, addr: SocketAddr) -> String {
    json!({
        "id": sandbox_id,
        "workspace_root": "/tmp/ws",
        "state": "ready",
        "daemon": {"host": addr.ip().to_string(), "port": 1},
        "daemon_http": {"host": addr.ip().to_string(), "port": addr.port()},
        "shared_base": null,
    })
    .to_string()
}

pub fn not_found_line(sandbox_id: &str) -> String {
    json!({
        "error": {
            "kind": "invalid_request",
            "message": format!("sandbox not found: {sandbox_id}"),
            "details": {},
        }
    })
    .to_string()
}

pub fn no_endpoint_line(sandbox_id: &str) -> String {
    json!({
        "id": sandbox_id,
        "workspace_root": "/tmp/ws",
        "state": "creating",
        "daemon": null,
        "daemon_http": null,
        "shared_base": null,
    })
    .to_string()
}

/// A fake `daemon_http`: `/health` answers ok, `/forward/...` echoes the
/// request (method, target, headers, body) as JSON, upgrade requests tunnel
/// a byte-echo loop, and targets containing `daemon-error` answer 403.
pub struct FakeDaemonHttp {
    pub addr: SocketAddr,
}

impl FakeDaemonHttp {
    pub async fn spawn() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind fake daemon");
        let addr = listener.local_addr().expect("fake daemon addr");
        tokio::spawn(async move {
            loop {
                let Ok((stream, _)) = listener.accept().await else {
                    break;
                };
                tokio::spawn(async move {
                    let service = service_fn(daemon_route);
                    let _ = hyper::server::conn::http1::Builder::new()
                        .serve_connection(TokioIo::new(stream), service)
                        .with_upgrades()
                        .await;
                });
            }
        });
        Self { addr }
    }
}

async fn daemon_route(
    mut req: Request<Incoming>,
) -> Result<Response<TestBody>, std::convert::Infallible> {
    let path = req.uri().path().to_owned();
    if path == "/health" {
        return Ok(json_response(
            StatusCode::OK,
            &json!({"status": "ok", "service": "daemon_http"}),
        ));
    }
    if path.contains("daemon-error") {
        let mut response = Response::new(full_body("isolated workspace has no reachable IP"));
        *response.status_mut() = StatusCode::FORBIDDEN;
        return Ok(response);
    }
    if req.headers().contains_key(http::header::UPGRADE) {
        let upgrade = hyper::upgrade::on(&mut req);
        tokio::spawn(async move {
            if let Ok(upgraded) = upgrade.await {
                let mut upgraded = TokioIo::new(upgraded);
                let mut buffer = [0u8; 1024];
                while let Ok(count) = upgraded.read(&mut buffer).await {
                    if count == 0 {
                        break;
                    }
                    if upgraded.write_all(&buffer[..count]).await.is_err() {
                        break;
                    }
                }
            }
        });
        let mut response = Response::new(empty_body());
        *response.status_mut() = StatusCode::SWITCHING_PROTOCOLS;
        response.headers_mut().insert(
            http::header::UPGRADE,
            http::HeaderValue::from_static("echo"),
        );
        response.headers_mut().insert(
            http::header::CONNECTION,
            http::HeaderValue::from_static("upgrade"),
        );
        return Ok(response);
    }
    let method = req.method().to_string();
    let target = req
        .uri()
        .path_and_query()
        .map(ToString::to_string)
        .unwrap_or_default();
    let mut headers = serde_json::Map::new();
    for (name, value) in req.headers() {
        headers.insert(
            name.as_str().to_owned(),
            Value::String(String::from_utf8_lossy(value.as_bytes()).into_owned()),
        );
    }
    let body = req
        .into_body()
        .collect()
        .await
        .map(|collected| String::from_utf8_lossy(&collected.to_bytes()).into_owned())
        .unwrap_or_default();
    Ok(json_response(
        StatusCode::OK,
        &json!({
            "method": method,
            "target": target,
            "headers": headers,
            "body": body,
        }),
    ))
}

fn json_response(status: StatusCode, value: &Value) -> Response<TestBody> {
    let mut response = Response::new(full_body(value.to_string()));
    *response.status_mut() = status;
    response.headers_mut().insert(
        http::header::CONTENT_TYPE,
        http::HeaderValue::from_static("application/json"),
    );
    response
}

/// Spawn the console server against `gateway_addr`, returning its address.
pub async fn spawn_console(
    gateway_addr: SocketAddr,
    assets_dir: Option<PathBuf>,
    rpc_timeout: Duration,
) -> SocketAddr {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind console");
    let addr = listener.local_addr().expect("console addr");
    let config = ConsoleConfig {
        bind: addr.to_string(),
        gateway: GatewayConfig {
            gateway_socket_path: PathBuf::from(gateway_addr.to_string()),
            gateway_auth_token: Some(TEST_AUTH_TOKEN.to_owned()),
        },
        assets_dir,
        rpc_timeout,
        // Shipped defaults; tests that tune a timeout go through the config.
        health_probe_timeout: Duration::from_secs(2),
        proxy_connect_timeout: Duration::from_secs(10),
        proxy_response_timeout: Duration::from_secs(30),
        endpoint_resolve_timeout: Duration::from_secs(5),
        endpoint_cache_ttl: Duration::from_secs(3),
    };
    tokio::spawn(server::serve(listener, AppState::new(config)));
    addr
}

pub async fn spawn_console_default(gateway_addr: SocketAddr) -> SocketAddr {
    spawn_console(gateway_addr, None, Duration::from_secs(10)).await
}

/// A gateway address that nothing listens on (bind, note the port, drop).
pub async fn closed_port() -> SocketAddr {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind probe listener");
    listener.local_addr().expect("probe addr")
}

pub async fn send_request(
    addr: SocketAddr,
    request: Request<TestBody>,
) -> (
    Response<Incoming>,
    hyper::client::conn::http1::SendRequest<TestBody>,
) {
    let stream = TcpStream::connect(addr).await.expect("connect console");
    let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(stream))
        .await
        .expect("console handshake");
    tokio::spawn(async move {
        let _ = connection.with_upgrades().await;
    });
    let response = sender
        .send_request(request)
        .await
        .expect("send console request");
    (response, sender)
}

pub async fn get(addr: SocketAddr, path: &str) -> Response<Incoming> {
    let request = Request::builder()
        .uri(path)
        .header(http::header::HOST, "console.test")
        .body(empty_body())
        .expect("build get request");
    send_request(addr, request).await.0
}

pub async fn post_rpc(addr: SocketAddr, body: &Value) -> Response<Incoming> {
    post_rpc_raw(addr, &body.to_string(), None).await
}

pub async fn post_rpc_sse(addr: SocketAddr, body: &Value) -> Response<Incoming> {
    post_rpc_raw(addr, &body.to_string(), Some("text/event-stream")).await
}

pub async fn post_rpc_raw(
    addr: SocketAddr,
    body: &str,
    accept: Option<&str>,
) -> Response<Incoming> {
    let mut builder = Request::builder()
        .method(http::Method::POST)
        .uri("/api/rpc")
        .header(http::header::HOST, "console.test")
        .header(http::header::CONTENT_TYPE, "application/json");
    if let Some(accept) = accept {
        builder = builder.header(http::header::ACCEPT, accept);
    }
    let request = builder
        .body(full_body(body.to_owned()))
        .expect("build rpc request");
    send_request(addr, request).await.0
}

pub async fn body_bytes(response: Response<Incoming>) -> Bytes {
    response
        .into_body()
        .collect()
        .await
        .expect("collect response body")
        .to_bytes()
}

pub async fn body_json(response: Response<Incoming>) -> Value {
    let bytes = body_bytes(response).await;
    serde_json::from_slice(&bytes).expect("response body is json")
}

pub async fn body_text(response: Response<Incoming>) -> String {
    let bytes = body_bytes(response).await;
    String::from_utf8_lossy(&bytes).into_owned()
}
