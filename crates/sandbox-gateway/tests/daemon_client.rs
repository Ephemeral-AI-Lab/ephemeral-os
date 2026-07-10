use std::net::TcpListener;
use std::thread;
use std::time::Duration;

use sandbox_gateway::TcpSandboxDaemonClient;
use sandbox_manager::{SandboxDaemonClient, SandboxDaemonEndpoint};
use sandbox_operation_contract::{OperationRequest, OperationScope};
use serde_json::json;

#[test]
fn explicit_timeout_bounds_stalled_daemon_response() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind listener");
    let port = listener.local_addr().expect("listener address").port();
    let server = thread::spawn(move || {
        let (_stream, _) = listener.accept().expect("accept daemon client");
        thread::sleep(Duration::from_millis(100));
    });
    let request = OperationRequest::new(
        "run_command",
        "request-1",
        OperationScope::sandbox("sandbox-1"),
        json!({}),
    );

    let error = TcpSandboxDaemonClient::new()
        .invoke(
            &SandboxDaemonEndpoint::new("127.0.0.1", port, "token"),
            request,
            Some(Duration::from_millis(20)),
        )
        .expect_err("stalled daemon response must time out");

    assert_eq!(
        error.to_string(),
        "sandbox daemon forwarding failed: daemon request timed out after 20 ms"
    );
    server.join().expect("daemon server thread");
}
