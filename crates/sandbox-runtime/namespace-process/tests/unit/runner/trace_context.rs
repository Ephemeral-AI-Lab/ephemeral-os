use crate::runner::protocol::{NamespaceRunnerRequest, TraceContext};

#[test]
fn old_runner_request_without_trace_context_deserializes() {
    let payload = serde_json::json!({
        "request_id": "cmd-old",
        "args": {"command": "true", "cwd": "."},
        "workspace_root": "/workspace",
        "layer_paths": ["/lower/base"],
        "upperdir": "/tmp/eos/upper",
        "workdir": "/tmp/eos/work",
        "ns_fds": {"user": 10, "mnt": 11, "pid": 12, "net": null},
        "cgroup_path": "/sys/fs/cgroup/eos",
        "timeout_seconds": 1.0
    });

    let request: NamespaceRunnerRequest =
        serde_json::from_value(payload).expect("old runner request decodes");

    assert_eq!(request.request_id, "cmd-old");
    assert_eq!(request.trace_context, None);
}

#[test]
fn runner_request_accepts_trace_context_as_optional_metadata() {
    let payload = serde_json::json!({
        "request_id": "cmd-trace",
        "args": {"command": "true"},
        "workspace_root": "/workspace",
        "layer_paths": [],
        "trace_context": {
            "traceparent": "invalid",
            "tracestate": "vendor=value"
        }
    });

    let request: NamespaceRunnerRequest =
        serde_json::from_value(payload).expect("trace context does not gate decoding");

    assert_eq!(
        request.trace_context,
        Some(TraceContext {
            traceparent: "invalid".to_owned(),
            tracestate: Some("vendor=value".to_owned()),
        })
    );
}
