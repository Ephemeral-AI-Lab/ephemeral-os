use sandbox_operation_client::{GatewayClient, GatewayClientError, MAX_REQUEST_BYTES};
use sandbox_operation_contract::{OperationRequest, OperationScope};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

async fn gateway(response: Vec<u8>) -> (String, tokio::task::JoinHandle<Value>) {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind fake gateway");
    let addr = listener.local_addr().expect("gateway address").to_string();
    let worker = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("accept client");
        let mut request = Vec::new();
        stream
            .read_to_end(&mut request)
            .await
            .expect("read request");
        stream.write_all(&response).await.expect("write response");
        serde_json::from_slice(&request).expect("request JSON")
    });
    (addr, worker)
}

fn request(args: Value) -> OperationRequest {
    OperationRequest::new(
        "list_sandboxes",
        "request-1",
        OperationScope::system(),
        args,
    )
}

#[tokio::test]
async fn send_adds_transport_fields_and_returns_json() {
    let (addr, worker) = gateway(b"{\"ok\":true}\n".to_vec()).await;
    let client = GatewayClient::new(addr, Some("secret".to_owned()));
    let response = client.send(&request(json!({}))).await.expect("response");
    let received = worker.await.expect("gateway task");

    assert_eq!(response, json!({"ok": true}));
    assert_eq!(received["_sandbox_gateway_auth_token"], "secret");
    assert_eq!(received["_stream_logs"], false);
    assert_eq!(received["op"], "list_sandboxes");
}

#[tokio::test]
async fn send_with_logs_delivers_each_log_before_the_response() {
    let response = b"cli_log(\"starting\")\ncli_log(\"ready\")\n{\"ok\":true}\n".to_vec();
    let (addr, worker) = gateway(response).await;
    let client = GatewayClient::new(addr, None);
    let mut logs = Vec::new();
    let response = client
        .send_with_logs(&request(json!({})), true, |line| logs.push(line.to_owned()))
        .await
        .expect("streamed response");
    worker.await.expect("gateway task");

    assert_eq!(logs, ["starting", "ready"]);
    assert_eq!(response, json!({"ok": true}));
}

#[tokio::test]
async fn oversized_encoded_request_is_rejected_before_connection() {
    assert_eq!(
        MAX_REQUEST_BYTES,
        sandbox_protocol::ProtocolLimits::DEFAULT_MAX_REQUEST_BYTES
    );
    let client = GatewayClient::new("127.0.0.1:1", None);
    let error = client
        .send(&request(json!({"content": "x".repeat(MAX_REQUEST_BYTES)})))
        .await
        .expect_err("oversized request");

    assert!(matches!(&error, GatewayClientError::Protocol(_)));
    assert_eq!(
        error.to_string(),
        format!("gateway request exceeded {MAX_REQUEST_BYTES} bytes")
    );
}
