use super::*;
use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::sync::Arc;
use std::time::{Duration, Instant};

use eos_trace::{
    encode_trace_batch, EventRecord, RequestId, SpanKind, SpanRecord, SpanUid, TraceBatch, TraceId,
    TraceRecord,
};
use serde_json::json;

use crate::trace_store::TraceEventRow;

#[test]
fn registry_round_trips_records_and_tokens() -> Result<()> {
    let dir = std::env::temp_dir().join(format!("eos-host-registry-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    let registry = SandboxRegistry::open(dir.clone())?;
    let record = SandboxRecord::new(
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
fn forward_request_persists_transport_events_and_strips_sidecar() -> Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let endpoint = listener.local_addr()?;
    let server = std::thread::spawn(move || -> Result<()> {
        let (mut stream, _) = listener.accept()?;
        let mut line = String::new();
        BufReader::new(stream.try_clone()?).read_line(&mut line)?;
        let request: serde_json::Value = serde_json::from_str(line.trim_end())?;
        let trace = request
            .get("trace")
            .and_then(serde_json::Value::as_object)
            .expect("host sends trace context");
        assert_eq!(trace["request_id"], json!("request-forward"));

        let trace_id = TraceId::parse(trace["trace_id"].as_str().expect("trace id"))?;
        let request_id = RequestId::parse(trace["request_id"].as_str().expect("request id"))?;
        let mut record = TraceRecord::new(trace_id, SpanUid::ROOT);
        record.request_id = Some(request_id);
        record.spans.push(SpanRecord::new(
            SpanUid::ROOT,
            None,
            "op_request",
            SpanKind::OpRequest,
            json!({"op": "sandbox.runtime.ready"}),
        ));
        record.spans.push(SpanRecord::new(
            SpanUid::new(2),
            Some(SpanUid::ROOT),
            "daemon.transport",
            SpanKind::DaemonTransport,
            json!({"listener_kind": "tcp"}),
        ));
        record.spans.push(SpanRecord::new(
            SpanUid::new(3),
            Some(SpanUid::ROOT),
            "dispatch",
            SpanKind::Dispatch,
            json!({"op": "sandbox.runtime.ready"}),
        ));
        record.spans.push(SpanRecord::new(
            SpanUid::new(4),
            Some(SpanUid::new(3)),
            "op.runtime.ready",
            SpanKind::Operation,
            json!({"op": "sandbox.runtime.ready"}),
        ));
        record.events.push(EventRecord::new(
            SpanUid::new(2),
            "accepted",
            "daemon.transport",
            json!({"listener_kind": "tcp", "request_bytes": line.len()}),
        ));
        record.events.push(EventRecord::new(
            SpanUid::new(2),
            "read_finished",
            "daemon.transport",
            json!({"request_bytes": line.len()}),
        ));
        record.events.push(EventRecord::new(
            SpanUid::new(2),
            "auth_checked",
            "daemon.transport",
            json!({"auth_required": true, "auth_ok": true}),
        ));
        record.events.push(EventRecord::new(
            SpanUid::new(2),
            "decoded",
            "daemon.transport",
            json!({"protocol_version": 1}),
        ));
        record.events.push(EventRecord::new(
            SpanUid::new(3),
            "dispatch_started",
            "daemon.dispatch",
            json!({"op": "sandbox.runtime.ready"}),
        ));
        record.events.push(EventRecord::new(
            SpanUid::new(3),
            "op_resolved",
            "daemon.dispatch",
            json!({"op": "sandbox.runtime.ready"}),
        ));
        record.events.push(EventRecord::new(
            SpanUid::new(4),
            "route_selected",
            "workspace.route",
            json!({"kind": "none"}),
        ));
        record.events.push(EventRecord::new(
            SpanUid::new(4),
            "ready_checked",
            "sandbox.runtime",
            json!({"ready": true}),
        ));
        record.events.push(EventRecord::new(
            SpanUid::new(2),
            "response_write_finished",
            "daemon.transport",
            json!({"response_bytes": 64}),
        ));
        let sidecar = base64::engine::general_purpose::STANDARD
            .encode(encode_trace_batch(&TraceBatch::single(record)));
        let response = json!({
            "success": true,
            "ready": true,
            "_trace_events": sidecar,
        });
        writeln!(stream, "{}", serde_json::to_string(&response)?)?;
        Ok(())
    });

    let dir = temp_host_dir("forward-trace");
    let store = Arc::new(TraceStore::open(&dir)?);
    let config = HostConfig {
        image: "test-image".to_owned(),
        platform: None,
        eosd_path: dir.join("eosd"),
        config_yaml_path: dir.join("config.yml"),
        remote_daemon_dir: PathBuf::from("/eos/runtime"),
        remote_eosd_path: PathBuf::from("/eos/eosd"),
        remote_config_path: PathBuf::from("/eos/config.yml"),
        tcp_port: endpoint.port(),
        ready_timeout: Duration::from_secs(1),
        request_timeout: Duration::from_secs(2),
        created_by: "test".to_owned(),
        state_dir: dir.clone(),
    };
    let record = SandboxRecord::new(
        "sb-forward".to_owned(),
        "sb-forward".to_owned(),
        "token".to_owned(),
        endpoint.port(),
        "test".to_owned(),
        Some(endpoint),
    );

    let mut trace = ForwardTraceContext::new("request-forward");
    trace.push_gateway_event(
        "gateway.transport",
        "accepted",
        json!({"surface": "client"}),
    );
    trace.push_gateway_event(
        "gateway.transport",
        "request_read",
        json!({"surface": "client", "request_bytes": 81}),
    );
    trace.push_gateway_event(
        "gateway.route",
        "route_selected",
        json!({"op": "sandbox.runtime.ready", "route": "daemon"}),
    );

    let response = forward_request(
        &record,
        &config,
        &store,
        &TraceExportDrainer::default(),
        trace,
        false,
        "sandbox.runtime.ready",
        "request-forward",
        &json!({"caller_id": "caller-1"}),
    )?;
    assert_eq!(response["_trace_events"], serde_json::Value::Null);
    assert_eq!(response["ready"], json!(true));

    let request = store
        .request_by_id("request-forward")?
        .expect("request row");
    assert_eq!(request.status.as_deref(), Some("ok"));
    let replay_trace_id = TraceId::parse(request.trace_id.clone())?;
    let replay_request_id = RequestId::parse(request.request_id.clone())?;
    store.append_trace_event(TraceEventInput {
        sandbox_id: &request.sandbox_id,
        trace_id: &replay_trace_id,
        request_id: Some(&replay_request_id),
        span_id: None,
        module: "gateway.transport",
        event: "response_written",
        details: json!({"response_bytes": 32}),
    })?;
    let events = store.events_for_trace(&request.trace_id)?;
    let event_names: Vec<_> = events
        .iter()
        .map(|event| (event.module.as_str(), event.event.as_str()))
        .collect();
    assert!(
        event_names.contains(&("host.transport", "connect_started")),
        "{event_names:?}"
    );
    assert!(
        event_names.contains(&("gateway.transport", "request_read")),
        "{event_names:?}"
    );
    assert!(
        event_names.contains(&("gateway.route", "route_selected")),
        "{event_names:?}"
    );
    assert!(
        event_names.contains(&("host.transport", "request_written")),
        "{event_names:?}"
    );
    assert!(
        event_names.contains(&("host.transport", "response_read")),
        "{event_names:?}"
    );
    assert!(
        event_names.contains(&("daemon.transport", "accepted")),
        "{event_names:?}"
    );
    assert_ordered_events(
        &event_names,
        &[
            ("gateway.transport", "accepted"),
            ("gateway.transport", "request_read"),
            ("gateway.route", "route_selected"),
            ("host.protocol", "forward_started"),
            ("host.transport", "connect_started"),
            ("host.transport", "request_written"),
            ("daemon.transport", "accepted"),
            ("daemon.transport", "read_finished"),
            ("daemon.transport", "auth_checked"),
            ("daemon.transport", "decoded"),
            ("daemon.dispatch", "dispatch_started"),
            ("daemon.dispatch", "op_resolved"),
            ("workspace.route", "route_selected"),
            ("sandbox.runtime", "ready_checked"),
            ("daemon.transport", "response_write_finished"),
            ("host.transport", "response_read"),
            ("gateway.transport", "response_written"),
        ],
    );

    server.join().expect("server thread")?;
    let _ = fs::remove_dir_all(dir);
    Ok(())
}

#[test]
fn malformed_sidecar_is_stripped_and_recorded_as_host_event() -> Result<()> {
    let dir = temp_host_dir("malformed-sidecar");
    let store = TraceStore::open(&dir)?;
    let endpoint = "127.0.0.1:9".parse().expect("discard port");
    let config = HostConfig {
        image: "test-image".to_owned(),
        platform: None,
        eosd_path: dir.join("eosd"),
        config_yaml_path: dir.join("config.yml"),
        remote_daemon_dir: PathBuf::from("/eos/runtime"),
        remote_eosd_path: PathBuf::from("/eos/eosd"),
        remote_config_path: PathBuf::from("/eos/config.yml"),
        tcp_port: 9,
        ready_timeout: Duration::from_millis(100),
        request_timeout: Duration::from_millis(100),
        created_by: "test".to_owned(),
        state_dir: dir.clone(),
    };
    let record = SandboxRecord::new(
        "sb-malformed-sidecar".to_owned(),
        "sb-malformed-sidecar".to_owned(),
        "token".to_owned(),
        9,
        "test".to_owned(),
        Some(endpoint),
    );
    let trace_id = TraceId::parse("trace-malformed-sidecar")?;
    let request_id = RequestId::parse("request-malformed-sidecar")?;
    let args = json!({});
    let mut tcp_line =
        encode_request_with_metadata("sandbox.runtime.ready", request_id.as_str(), &args, None);
    tcp_line.push(b'\n');
    let attempt = ForwardAttempt {
        record: &record,
        config: &config,
        trace_store: &store,
        trace_id: trace_id.clone(),
        request_id,
        mutates_state: false,
        tcp_line,
        op: "sandbox.runtime.ready",
        invocation_id: "malformed-sidecar",
        args: &args,
    };
    let mut response = json!({"success": true, "_trace_events": "not base64"});

    let sidecar = ingest_and_strip_sidecar(&attempt, &mut response);

    assert!(sidecar.present);
    assert!(!sidecar.ingested);
    assert!(response.get("_trace_events").is_none());
    let events = store.events_for_trace(trace_id.as_str())?;
    assert_event(&events, "host.transport", "sidecar_decode_failed");
    assert!(
        events.iter().any(|event| {
            event.event == "sidecar_decode_failed"
                && serde_json::from_str::<serde_json::Value>(&event.details_json)
                    .ok()
                    .and_then(|details| details.get("error_kind").cloned())
                    == Some(json!("invalid_base64"))
        }),
        "sidecar_decode_failed details missing: {events:?}"
    );

    let _ = fs::remove_dir_all(dir);
    Ok(())
}

#[test]
fn tcp_once_records_transport_failure_events() -> Result<()> {
    let cases = [
        (
            "empty-response",
            "empty_response",
            Box::new(|stream: std::net::TcpStream| {
                let _ = stream.shutdown(std::net::Shutdown::Write);
                std::thread::sleep(Duration::from_millis(50));
            }) as Box<dyn FnOnce(std::net::TcpStream) + Send>,
        ),
        (
            "decode-failed",
            "decode_failed",
            Box::new(|mut stream: std::net::TcpStream| {
                let _ = writeln!(stream, "not json");
            }),
        ),
        (
            "read-timeout",
            "read_failed",
            Box::new(|_stream: std::net::TcpStream| {
                std::thread::sleep(Duration::from_millis(250));
            }),
        ),
    ];

    for (name, expected_event, handler) in cases {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let endpoint = listener.local_addr()?;
        std::thread::spawn(move || {
            if let Ok((stream, _)) = listener.accept() {
                handler(stream);
            }
        });
        let (store, trace_id) = run_tcp_once_failure(name, endpoint)?;
        let events = store.events_for_trace(trace_id.as_str())?;
        assert!(
            events
                .iter()
                .any(|event| event.module == "host.transport" && event.event == expected_event),
            "{name}: {events:?}"
        );
    }

    let endpoint = "127.0.0.1:9".parse().expect("discard port");
    let (store, trace_id) = run_tcp_once_failure("connect-refused", endpoint)?;
    let events = store.events_for_trace(trace_id.as_str())?;
    assert!(
        events
            .iter()
            .any(|event| event.module == "host.transport" && event.event == "connect_failed"),
        "{events:?}"
    );
    Ok(())
}

#[test]
fn host_transport_records_retry_endpoint_refresh_write_and_connect_timeout_facts() -> Result<()> {
    let dir = temp_host_dir("transport-edge-facts");
    let store = TraceStore::open(&dir)?;
    let endpoint: std::net::SocketAddr = "127.0.0.1:9".parse().expect("discard port");
    let refreshed_endpoint: std::net::SocketAddr = "127.0.0.1:10".parse().expect("refresh port");
    let config = HostConfig {
        image: "test-image".to_owned(),
        platform: None,
        eosd_path: dir.join("eosd"),
        config_yaml_path: dir.join("config.yml"),
        remote_daemon_dir: PathBuf::from("/eos/runtime"),
        remote_eosd_path: PathBuf::from("/eos/eosd"),
        remote_config_path: PathBuf::from("/eos/config.yml"),
        tcp_port: endpoint.port(),
        ready_timeout: Duration::from_millis(100),
        request_timeout: Duration::from_millis(100),
        created_by: "test".to_owned(),
        state_dir: dir.clone(),
    };
    let record = SandboxRecord::new(
        "sb-transport-edges".to_owned(),
        "sb-transport-edges".to_owned(),
        "token".to_owned(),
        endpoint.port(),
        "test".to_owned(),
        Some(endpoint),
    );
    let trace_id = TraceId::parse("trace-transport-edges")?;
    let request_id = RequestId::parse("request-transport-edges")?;
    let args = json!({});
    let mut tcp_line =
        encode_request_with_metadata("sandbox.runtime.ready", request_id.as_str(), &args, None);
    tcp_line.push(b'\n');
    let attempt = ForwardAttempt {
        record: &record,
        config: &config,
        trace_store: &store,
        trace_id: trace_id.clone(),
        request_id,
        mutates_state: false,
        tcp_line,
        op: "sandbox.runtime.ready",
        invocation_id: "transport-edge-test",
        args: &args,
    };

    let write_failure = ClientError::Write(std::io::Error::new(
        std::io::ErrorKind::BrokenPipe,
        "closed",
    ));
    record_client_error(&attempt, endpoint, 0, Instant::now(), &write_failure);
    let connect_timeout = ClientError::Connect {
        addr: endpoint,
        source: std::io::Error::new(std::io::ErrorKind::TimedOut, "connect timed out"),
    };
    record_client_error(&attempt, endpoint, 1, Instant::now(), &connect_timeout);
    record_endpoint_refreshed(&attempt, endpoint, refreshed_endpoint);
    let _ = tcp_with_connect_backoff(&attempt, endpoint);

    let events = store.events_for_trace(trace_id.as_str())?;
    assert_event(&events, "host.transport", "write_failed");
    assert_event(&events, "host.transport", "connect_timeout");
    assert_event(&events, "host.transport", "endpoint_refreshed");
    assert_event(&events, "host.transport", "retry_scheduled");
    assert!(
        events.iter().any(|event| {
            if event.module != "host.transport" || event.event != "connect_timeout" {
                return false;
            }
            serde_json::from_str::<serde_json::Value>(&event.details_json)
                .ok()
                .and_then(|details| details.get("error_kind").cloned())
                == Some(json!("connect_timeout"))
        }),
        "connect_timeout details missing: {events:?}"
    );

    let _ = fs::remove_dir_all(dir);
    Ok(())
}

fn run_tcp_once_failure(
    name: &str,
    endpoint: std::net::SocketAddr,
) -> Result<(TraceStore, TraceId)> {
    let dir = temp_host_dir(name);
    let store = TraceStore::open(&dir)?;
    let config = HostConfig {
        image: "test-image".to_owned(),
        platform: None,
        eosd_path: dir.join("eosd"),
        config_yaml_path: dir.join("config.yml"),
        remote_daemon_dir: PathBuf::from("/eos/runtime"),
        remote_eosd_path: PathBuf::from("/eos/eosd"),
        remote_config_path: PathBuf::from("/eos/config.yml"),
        tcp_port: endpoint.port(),
        ready_timeout: Duration::from_millis(100),
        request_timeout: Duration::from_millis(100),
        created_by: "test".to_owned(),
        state_dir: dir,
    };
    let record = SandboxRecord::new(
        format!("sb-{name}"),
        format!("sb-{name}"),
        "token".to_owned(),
        endpoint.port(),
        "test".to_owned(),
        Some(endpoint),
    );
    let trace_id = TraceId::parse(format!("trace-{name}")).expect("trace id");
    let request_id = RequestId::parse(format!("request-{name}")).expect("request id");
    let args = json!({});
    let mut tcp_line =
        encode_request_with_metadata("sandbox.runtime.ready", request_id.as_str(), &args, None);
    tcp_line.push(b'\n');
    let attempt = ForwardAttempt {
        record: &record,
        config: &config,
        trace_store: &store,
        trace_id: trace_id.clone(),
        request_id,
        mutates_state: false,
        tcp_line,
        op: "sandbox.runtime.ready",
        invocation_id: "failure-test",
        args: &args,
    };
    let _ = tcp_once(&attempt, endpoint, 0);
    Ok((store, trace_id))
}

fn assert_event(events: &[TraceEventRow], module: &str, event: &str) {
    assert!(
        events
            .iter()
            .any(|row| row.module == module && row.event == event),
        "missing {module}/{event}: {events:?}"
    );
}

fn temp_host_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("eos-host-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("create temp host dir");
    dir
}

fn assert_ordered_events(actual: &[(&str, &str)], expected: &[(&str, &str)]) {
    let mut next = 0;
    for event in actual {
        if expected.get(next) == Some(event) {
            next += 1;
        }
    }
    assert_eq!(
        next,
        expected.len(),
        "missing ordered replay suffix starting at {:?}; actual events: {actual:?}",
        expected.get(next)
    );
}
