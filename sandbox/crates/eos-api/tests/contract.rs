//! `eos-api` contract conformance (SPEC §9.3): the router covers every
//! catalog entry, refuses non-public ops on the client socket, and produces
//! the documented API error kinds — proven over a real Unix-socket round trip
//! with a stub engine (no docker required).

use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::sync::Arc;

use serde_json::{json, Value};

use eos_api::public::{Catalog, Route, Visibility};
use eos_api::router::{self, Engine, Surface};
use eos_api::server;
use eos_api::wire::{parse_request, ClientRequest};
use eos_sandbox_host::ForwardError;

const KNOWN_SANDBOX: &str = "sb-stub";

struct StubEngine;

impl Engine for StubEngine {
    fn acquire(&self) -> anyhow::Result<String> {
        Ok(KNOWN_SANDBOX.to_owned())
    }

    fn release(&self, sandbox_id: &str) -> bool {
        sandbox_id == KNOWN_SANDBOX
    }

    fn status(&self, sandbox_id: &str) -> Option<Value> {
        (sandbox_id == KNOWN_SANDBOX)
            .then(|| json!({"success": true, "sandbox_id": sandbox_id, "daemon": {"ready": true}}))
    }

    fn list(&self) -> Vec<Value> {
        vec![json!({"sandbox_id": KNOWN_SANDBOX})]
    }

    fn forward(
        &self,
        sandbox_id: &str,
        mutates_state: bool,
        op: &str,
        invocation_id: &str,
        _args: &Value,
    ) -> Option<Result<Value, ForwardError>> {
        if sandbox_id != KNOWN_SANDBOX {
            return None;
        }
        Some(match op {
            "sandbox.file.write" => Err(ForwardError::UncertainOutcome("stub".into())),
            "sandbox.command.poll" => Err(ForwardError::SandboxUnavailable("stub".into())),
            _ => Ok(json!({
                "success": true,
                "forwarded_op": op,
                "mutates_state": mutates_state,
                "invocation_id": invocation_id,
            })),
        })
    }
}

fn request(op: &str, sandbox_id: Option<&str>) -> ClientRequest {
    let mut envelope =
        json!({"op": op, "invocation_id": "00000000000000000000000000000001", "args": {}});
    if let Some(id) = sandbox_id {
        envelope["sandbox_id"] = json!(id);
    }
    parse_request(&serde_json::to_vec(&envelope).expect("encode")).expect("parse")
}

fn kind(response: &Value) -> Option<&str> {
    response.get("error")?.get("kind")?.as_str()
}

#[test]
fn router_covers_every_catalog_entry() {
    let catalog = Catalog::load_builtin().expect("catalog loads and every entry routes");
    let engine = StubEngine;
    for entry in catalog.entries() {
        let response = router::handle(
            &catalog,
            &engine,
            Surface::Admin,
            &request(&entry.name, Some(KNOWN_SANDBOX)),
        );
        if matches!(entry.visibility, Visibility::Internal | Visibility::Test) {
            assert_eq!(kind(&response), Some("forbidden"), "{}", entry.name);
            continue;
        }
        assert_ne!(
            kind(&response),
            Some("unknown_op"),
            "catalog entry must route: {}",
            entry.name
        );
    }
}

#[test]
fn daemon_ops_route_under_both_spellings() {
    let catalog = Catalog::load_builtin().expect("catalog");
    let engine = StubEngine;
    for (spelling, canonical) in [
        ("sandbox.file.read", "sandbox.file.read"),
        ("api.v1.read_file", "sandbox.file.read"),
        ("sandbox.call.heartbeat", "sandbox.call.heartbeat"),
        ("api.v1.heartbeat", "sandbox.call.heartbeat"),
    ] {
        let entry = catalog.lookup(spelling).expect("spelling resolves");
        assert_eq!(entry.name, canonical);
        assert_eq!(entry.route, Route::Daemon);
        let response = router::handle(
            &catalog,
            &engine,
            Surface::Client,
            &request(spelling, Some(KNOWN_SANDBOX)),
        );
        // The daemon's response comes back verbatim under either spelling.
        assert_eq!(response["forwarded_op"], json!(spelling));
    }
}

#[test]
fn client_socket_refuses_non_public_ops() {
    let catalog = Catalog::load_builtin().expect("catalog");
    let engine = StubEngine;
    for (op, surface, expected_forbidden) in [
        // Operator ops: forbidden on the client socket, served on admin.
        ("sandbox.checkpoint.layer_metrics", Surface::Client, true),
        ("sandbox.checkpoint.layer_metrics", Surface::Admin, false),
        ("sandbox.run.cancel_all", Surface::Client, true),
        // Internal and test ops: forbidden everywhere.
        ("sandbox.runtime.ready", Surface::Client, true),
        ("sandbox.runtime.ready", Surface::Admin, true),
        ("sandbox.isolation.test_reset", Surface::Admin, true),
        // Public ops pass the client gate.
        ("sandbox.file.read", Surface::Client, false),
        ("sandbox.acquire", Surface::Client, false),
    ] {
        let response = router::handle(
            &catalog,
            &engine,
            surface,
            &request(op, Some(KNOWN_SANDBOX)),
        );
        assert_eq!(
            kind(&response) == Some("forbidden"),
            expected_forbidden,
            "visibility gate mismatch for {op} on {surface:?}: {response}"
        );
    }
}

#[test]
fn api_error_kinds_are_produced() {
    let catalog = Catalog::load_builtin().expect("catalog");
    let engine = StubEngine;
    let cases = [
        ("api.totally.bogus.op", Some(KNOWN_SANDBOX), "unknown_op"),
        ("sandbox.file.read", Some("sb-missing"), "unknown_sandbox"),
        ("sandbox.file.read", None, "invalid_envelope"),
        (
            "sandbox.file.write",
            Some(KNOWN_SANDBOX),
            "uncertain_outcome",
        ),
        (
            "sandbox.command.poll",
            Some(KNOWN_SANDBOX),
            "sandbox_unavailable",
        ),
    ];
    for (op, sandbox, expected) in cases {
        let response = router::handle(&catalog, &engine, Surface::Client, &request(op, sandbox));
        assert_eq!(kind(&response), Some(expected), "{op}: {response}");
    }
    // Dynamic plugin ops forward without a catalog entry.
    let response = router::handle(
        &catalog,
        &engine,
        Surface::Client,
        &request("plugin.lsp.query", Some(KNOWN_SANDBOX)),
    );
    assert_eq!(response["forwarded_op"], json!("plugin.lsp.query"));
    assert_eq!(response["mutates_state"], json!(true));
}

#[test]
fn unix_socket_round_trip_serves_one_request_per_connection() {
    let socket = test_socket_path("round-trip");
    let catalog = Arc::new(Catalog::load_builtin().expect("catalog"));
    let listen = socket.clone();
    std::thread::spawn(move || {
        let _ = server::serve(&listen, catalog, Arc::new(StubEngine));
    });
    let response = round_trip_when_ready(
        &socket,
        b"{\"op\":\"sandbox.acquire\",\"invocation_id\":\"i1\",\"args\":{}}\n",
    );
    assert_eq!(response["success"], json!(true));
    assert_eq!(response["sandbox_id"], json!(KNOWN_SANDBOX));

    // Malformed JSON surfaces bad_json; the server half-closes after one line.
    let response = round_trip_when_ready(&socket, b"{not json\n");
    assert_eq!(kind(&response), Some("bad_json"));

    // Operator ops are forbidden on the client socket but served on admin.
    let metrics = b"{\"op\":\"sandbox.checkpoint.layer_metrics\",\"sandbox_id\":\"sb-stub\",\"invocation_id\":\"i2\",\"args\":{}}\n";
    let response = round_trip_when_ready(&socket, metrics);
    assert_eq!(kind(&response), Some("forbidden"));
    let response = round_trip_when_ready(&server::admin_socket_path(&socket), metrics);
    assert_eq!(
        response["forwarded_op"],
        json!("sandbox.checkpoint.layer_metrics")
    );

    let _ = std::fs::remove_file(server::admin_socket_path(&socket));
    let _ = std::fs::remove_file(&socket);
}

fn test_socket_path(tag: &str) -> PathBuf {
    std::env::temp_dir().join(format!(
        "eos-api-contract-{tag}-{}.sock",
        std::process::id()
    ))
}

fn round_trip_when_ready(socket: &PathBuf, line: &[u8]) -> Value {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    let mut stream = loop {
        match UnixStream::connect(socket) {
            Ok(stream) => break stream,
            Err(err) => {
                assert!(
                    std::time::Instant::now() < deadline,
                    "server socket {} never came up: {err}",
                    socket.display()
                );
                std::thread::sleep(std::time::Duration::from_millis(25));
            }
        }
    };
    stream.write_all(line).expect("write request");
    stream.flush().ok();
    let mut reader = BufReader::new(stream);
    let mut response = String::new();
    reader.read_line(&mut response).expect("read response");
    serde_json::from_str(response.trim_end()).expect("decode response")
}
