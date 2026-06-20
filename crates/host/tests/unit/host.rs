use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use serde_json::{json, Value};

use crate::container::override_docker_command_for_tests;
use crate::daemon_wire::{encode_request_with_metadata, ClientError};
use crate::service::forward::{forward_request, tcp_once, ForwardAttempt, ForwardRequestInput};
use crate::service::registry::{SandboxRecord, SandboxRegistry};
use crate::service::{HostConfig, SandboxHost};

#[test]
fn acquire_workspace_root_defaults_to_testbed_and_allows_absolute_override() -> Result<()> {
    assert_eq!(
        super::workspace_root_from_args(&json!({}))?,
        PathBuf::from("/testbed")
    );
    assert_eq!(
        super::workspace_root_from_args(&json!({"workspace_root": "/workspace"}))?,
        PathBuf::from("/workspace")
    );
    assert!(
        super::workspace_root_from_args(&json!({"workspace_root": "relative"})).is_err(),
        "host acquire workspace_root must be absolute"
    );
    Ok(())
}

#[test]
fn registry_round_trips_records_and_tokens() -> Result<()> {
    let dir = std::env::temp_dir().join(format!("eos-host-registry-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    let registry = SandboxRegistry::open(dir.clone())?;
    let record = sandbox_record(
        "sb-1".into(),
        "sb-1".into(),
        "tok".into(),
        37_657,
        "test".into(),
        None,
    );
    registry.insert(record)?;
    let record = registry.get("sb-1").expect("inserted record");
    assert_eq!(registry.load_token("sb-1")?, "tok");
    assert!(registry.get("sb-1").is_some());
    assert_eq!(registry.list().len(), 1);

    record.cache_endpoint("127.0.0.1:9999".parse().expect("addr"));
    assert!(record.cached_endpoint().is_some());
    record.invalidate_endpoint();
    assert!(record.cached_endpoint().is_none());

    assert!(registry.remove("sb-1").is_some());
    assert!(registry.get("sb-1").is_none());
    assert!(registry.load_token("sb-1").is_err());
    let _ = fs::remove_dir_all(dir);
    Ok(())
}

#[test]
fn sandbox_lifecycle_respawn_waits_for_active_forward() -> Result<()> {
    let record = Arc::new(sandbox_record(
        "sb-lifecycle".to_owned(),
        "sb-lifecycle".to_owned(),
        "token".to_owned(),
        37_657,
        "test".to_owned(),
        None,
    ));
    let forward = record.begin_forward();
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let (acquired_tx, acquired_rx) = std::sync::mpsc::channel();
    let waiting_record = Arc::clone(&record);
    let handle = std::thread::spawn(move || {
        started_tx.send(()).expect("send start");
        let _respawn = waiting_record.begin_respawn();
        acquired_tx.send(()).expect("send acquired");
    });

    started_rx.recv_timeout(Duration::from_secs(1))?;
    assert!(
        acquired_rx.recv_timeout(Duration::from_millis(50)).is_err(),
        "respawn acquired lifecycle while a forward was still active"
    );

    drop(forward);
    acquired_rx.recv_timeout(Duration::from_secs(1))?;
    handle.join().expect("respawn waiter joins");
    Ok(())
}

#[test]
fn sandbox_lifecycle_forward_waits_for_active_respawn() -> Result<()> {
    let record = Arc::new(sandbox_record(
        "sb-lifecycle-respawn".to_owned(),
        "sb-lifecycle-respawn".to_owned(),
        "token".to_owned(),
        37_657,
        "test".to_owned(),
        None,
    ));
    let respawn = record.begin_respawn();
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let (acquired_tx, acquired_rx) = std::sync::mpsc::channel();
    let waiting_record = Arc::clone(&record);
    let handle = std::thread::spawn(move || {
        started_tx.send(()).expect("send start");
        let _forward = waiting_record.begin_forward();
        acquired_tx.send(()).expect("send acquired");
    });

    started_rx.recv_timeout(Duration::from_secs(1))?;
    assert!(
        acquired_rx.recv_timeout(Duration::from_millis(50)).is_err(),
        "forward acquired lifecycle while a respawn was still active"
    );

    drop(respawn);
    acquired_rx.recv_timeout(Duration::from_secs(1))?;
    handle.join().expect("forward waiter joins");
    Ok(())
}

#[test]
fn forward_request_sends_daemon_request_metadata_only() -> Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let endpoint = listener.local_addr()?;
    let server = std::thread::spawn(move || -> Result<()> {
        let (mut stream, _) = listener.accept()?;
        let mut line = String::new();
        BufReader::new(stream.try_clone()?).read_line(&mut line)?;
        let request: serde_json::Value = serde_json::from_str(line.trim_end())?;
        assert_eq!(request["op"], json!("sandbox.runtime.ready"));
        assert_eq!(request["request_id"], json!("request-forward"));
        assert_eq!(request["args"]["request_id"], json!("request-forward"));
        let response = json!({
            "status": "ok",
            "result": {"ready": true},
            "meta": {
                "envelope_version": 2,
                "op": "sandbox.runtime.ready",
                "request_id": "request-forward",
                "duration_ms": 0.0,
                "resource_summary": {"fields": {}},
                "warnings": []
            }
        });
        writeln!(stream, "{}", serde_json::to_string(&response)?)?;
        Ok(())
    });

    let dir = temp_host_dir("forward-metadata");
    let config = host_config(&dir, endpoint.port());
    let record = Arc::new(sandbox_record(
        "sb-forward".to_owned(),
        "sb-forward".to_owned(),
        "token".to_owned(),
        endpoint.port(),
        "test".to_owned(),
        Some(endpoint),
    ));

    let response = forward_request(ForwardRequestInput {
        record,
        config: &config,
        op: "sandbox.runtime.ready",
        request_id: "request-forward",
        args: &json!({"probe": "ready"}),
    })?;
    assert_eq!(response["result"]["ready"], json!(true));
    assert_eq!(response["meta"]["request_id"], json!("request-forward"));

    server.join().expect("server thread")?;
    let _ = fs::remove_dir_all(dir);
    Ok(())
}

#[test]
fn decode_client_errors_do_not_format_raw_response_body() {
    let raw = "{\"status\":\"error\",\"token\":\"super-secret\"";
    let source = serde_json::from_str::<Value>(raw).expect_err("invalid json");
    let error = ClientError::Decode {
        raw_len: raw.len(),
        raw_sha256: crate::daemon_wire::sha256_hex(raw.as_bytes()),
        source,
    };
    let message = error.to_string();

    assert!(
        !message.contains("super-secret"),
        "decode error display must not expose daemon response bytes: {message}"
    );
    assert!(
        message.contains("raw_len=") && message.contains("raw_sha256="),
        "decode error display should preserve non-secret diagnostics: {message}"
    );
}

#[test]
fn tcp_once_returns_transport_errors_without_event_store() -> Result<()> {
    type FailureHandler = Box<dyn FnOnce(TcpStream) + Send>;
    type ErrorMatcher = fn(&ClientError) -> bool;
    type FailureCase = (&'static str, FailureHandler, ErrorMatcher);

    let cases: [FailureCase; 3] = [
        (
            "empty-response",
            Box::new(|stream: TcpStream| {
                let _ = stream.shutdown(std::net::Shutdown::Write);
                std::thread::sleep(Duration::from_millis(50));
            }) as FailureHandler,
            |error: &ClientError| matches!(error, ClientError::EmptyResponse),
        ),
        (
            "decode-failed",
            Box::new(|mut stream: TcpStream| {
                let _ = writeln!(stream, "not json");
            }),
            |error: &ClientError| matches!(error, ClientError::Decode { .. }),
        ),
        (
            "read-timeout",
            Box::new(|_stream: TcpStream| {
                std::thread::sleep(Duration::from_millis(250));
            }),
            |error: &ClientError| matches!(error, ClientError::Read(_)),
        ),
    ];

    for (name, handler, matches_error) in cases {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let endpoint = listener.local_addr()?;
        std::thread::spawn(move || {
            if let Ok((stream, _)) = listener.accept() {
                handler(stream);
            }
        });
        let error = run_tcp_once_failure(name, endpoint)?;
        assert!(matches_error(&error), "{name}: {error:?}");
    }

    let endpoint = "127.0.0.1:9".parse().expect("discard port");
    let error = run_tcp_once_failure("connect-refused", endpoint)?;
    assert!(matches!(error, ClientError::Connect { .. }), "{error:?}");
    Ok(())
}

#[test]
#[cfg(unix)]
fn release_keeps_registry_entry_when_container_removal_fails() -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let dir = temp_host_dir("release-removal-failure");
    let registry = Arc::new(SandboxRegistry::open(dir.clone())?);
    registry.insert(sandbox_record(
        "sb-release-failure".to_owned(),
        "sb-release-failure".to_owned(),
        "token".to_owned(),
        37_657,
        "test".to_owned(),
        None,
    ))?;
    let host = SandboxHost {
        config: host_config(&dir, 37_657),
        config_yaml: String::new(),
        registry: Arc::clone(&registry),
    };
    let docker = dir.join("docker");
    fs::write(
        &docker,
        "#!/bin/sh\necho simulated docker removal failure >&2\nexit 42\n",
    )?;
    let mut permissions = fs::metadata(&docker)?.permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(&docker, permissions)?;
    let _docker = override_docker_command_for_tests(docker);

    let error = host
        .release_with_args("sb-release-failure", &json!({}))
        .expect_err("container removal failure must be reported");
    assert!(
        error
            .to_string()
            .contains("remove sandbox container sb-release-failure"),
        "unexpected release failure: {error:#}"
    );
    assert!(
        registry.get("sb-release-failure").is_some(),
        "registry entry must remain retryable after cleanup failure"
    );
    assert_eq!(registry.load_token("sb-release-failure")?, "token");

    let _ = fs::remove_dir_all(dir);
    Ok(())
}

fn run_tcp_once_failure(name: &str, endpoint: std::net::SocketAddr) -> Result<ClientError> {
    let dir = temp_host_dir(name);
    let config = host_config(&dir, endpoint.port());
    let record = sandbox_record(
        format!("sb-{name}"),
        format!("sb-{name}"),
        "token".to_owned(),
        endpoint.port(),
        "test".to_owned(),
        Some(endpoint),
    );
    let args = json!({});
    let mut tcp_line = encode_request_with_metadata("sandbox.runtime.ready", name, &args, None);
    tcp_line.push(b'\n');
    let attempt = ForwardAttempt {
        record: &record,
        config: &config,
        tcp_line,
        op: "sandbox.runtime.ready",
        request_id: name,
        args: &args,
    };
    let error = tcp_once(&attempt, endpoint, 0).expect_err("tcp_once should fail in this test");
    let _ = fs::remove_dir_all(dir);
    Ok(error)
}

fn host_config(dir: &std::path::Path, tcp_port: u16) -> HostConfig {
    HostConfig {
        image: "test-image".to_owned(),
        platform: None,
        docker_privileged: true,
        eosd_path: dir.join("eosd"),
        config_yaml_path: dir.join("config.yml"),
        remote_daemon_dir: PathBuf::from("/eos/runtime"),
        remote_eosd_path: PathBuf::from("/eos/eosd"),
        remote_config_path: PathBuf::from("/eos/config.yml"),
        tcp_port,
        ready_timeout: Duration::from_millis(100),
        request_timeout: Duration::from_millis(100),
        created_by: "test".to_owned(),
        state_dir: dir.to_path_buf(),
    }
}

fn temp_host_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("eos-host-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("create temp host dir");
    dir
}

fn sandbox_record(
    sandbox_id: String,
    container: String,
    token: String,
    tcp_port: u16,
    created_by: String,
    endpoint: Option<std::net::SocketAddr>,
) -> SandboxRecord {
    SandboxRecord::new_with_forward_token(
        sandbox_id,
        container,
        token.clone(),
        token,
        tcp_port,
        created_by,
        endpoint,
    )
}
