//! Command wrapper tests for the public sandbox tool API.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Mutex;

use async_trait::async_trait;
use eos_sandbox_port::{
    cancel_command_session, collect_command_completions, exec_command, exec_dispatch_timeout,
    exec_stdin, read_command_progress, CommandSessionCancelRequest, DaemonOp, ExecCommandRequest,
    ExecStdinRequest, ReadCommandProgressRequest, SandboxPortError, SandboxRequestBase,
    SandboxTransport,
};
use eos_types::{JsonObject, SandboxId};
use serde_json::{json, Value};

#[derive(Debug, Clone, PartialEq)]
struct RecordedCall {
    sandbox_id: SandboxId,
    op: DaemonOp,
    payload: JsonObject,
    timeout_s: u32,
}

#[derive(Debug)]
struct RecordingTransport {
    response: Result<JsonObject, SandboxPortError>,
    calls: Mutex<Vec<RecordedCall>>,
}

impl RecordingTransport {
    fn ok(response: Value) -> Self {
        Self {
            response: Ok(obj(response)),
            calls: Mutex::new(Vec::new()),
        }
    }

    fn calls(&self) -> Vec<RecordedCall> {
        self.calls.lock().unwrap().clone()
    }
}

#[async_trait]
impl SandboxTransport for RecordingTransport {
    async fn call(
        &self,
        sandbox_id: &SandboxId,
        op: DaemonOp,
        payload: JsonObject,
        timeout_s: u32,
    ) -> Result<JsonObject, SandboxPortError> {
        self.calls.lock().unwrap().push(RecordedCall {
            sandbox_id: sandbox_id.clone(),
            op,
            payload,
            timeout_s,
        });
        self.response.clone()
    }
}

fn obj(value: Value) -> JsonObject {
    match value {
        Value::Object(map) => map,
        _ => panic!("test value is not an object"),
    }
}

fn base() -> SandboxRequestBase {
    SandboxRequestBase::new(
        "agent-1",
        "test op",
        Some("inv-command".parse().expect("invocation id")),
    )
}

fn sandbox_id() -> SandboxId {
    "sandbox-command".parse().expect("sandbox id")
}

fn command_response() -> Value {
    json!({
        "status": "completed",
        "exit_code": 0,
        "output": {"stdout": "ok", "stderr": ""},
        "command_session_id": "cmd-1",
    })
}

#[tokio::test]
async fn exec_command_builds_payload_and_uses_exec_timeout() {
    let transport = RecordingTransport::ok(command_response());
    let request = ExecCommandRequest {
        base: base(),
        cmd: "printf ok".to_owned(),
        yield_time_ms: Some(25),
        timeout: Some(7),
    };

    let result = exec_command(&transport, &sandbox_id(), &request)
        .await
        .expect("exec command");

    assert_eq!(result.status, "completed");
    assert_eq!(result.output.stdout, "ok");
    assert_eq!(
        result.command_session_id.as_ref().map(ToString::to_string),
        Some("cmd-1".to_owned())
    );
    let calls = transport.calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].op, DaemonOp::ExecCommand);
    assert_eq!(calls[0].timeout_s, exec_dispatch_timeout(Some(7)));
    assert_eq!(calls[0].payload["caller_id"], json!("agent-1"));
    assert_eq!(calls[0].payload["invocation_id"], json!("inv-command"));
    assert_eq!(calls[0].payload["cmd"], json!("printf ok"));
    assert_eq!(calls[0].payload["yield_time_ms"], json!(25));
    assert_eq!(calls[0].payload["timeout"], json!(7));
}

#[tokio::test]
async fn exec_stdin_builds_input_only_payload() {
    let transport = RecordingTransport::ok(command_response());
    let request = ExecStdinRequest {
        base: base(),
        command_session_id: "cmd-1".parse().expect("command session id"),
        chars: "input".to_owned(),
        yield_time_ms: Some(10),
    };

    exec_stdin(&transport, &sandbox_id(), &request)
        .await
        .expect("stdin write");

    let calls = transport.calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].op, DaemonOp::ExecStdin);
    assert_eq!(calls[0].payload["command_session_id"], json!("cmd-1"));
    assert_eq!(calls[0].payload["chars"], json!("input"));
    assert_eq!(calls[0].payload["yield_time_ms"], json!(10));
    assert!(!calls[0].payload.contains_key("terminate"));
}

#[tokio::test]
async fn read_command_progress_uses_command_read_progress_op() {
    let transport = RecordingTransport::ok(command_response());
    let request = ReadCommandProgressRequest {
        base: base(),
        command_session_id: "cmd-1".parse().expect("command session id"),
        last_n_lines: 25,
    };

    read_command_progress(&transport, &sandbox_id(), &request)
        .await
        .expect("read progress");

    let calls = transport.calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].op, DaemonOp::CommandReadProgress);
    assert_eq!(calls[0].payload["command_session_id"], json!("cmd-1"));
    assert_eq!(calls[0].payload["last_n_lines"], json!(25));
}

#[tokio::test]
async fn cancel_command_session_uses_command_cancel_op() {
    let transport = RecordingTransport::ok(command_response());
    let request = CommandSessionCancelRequest {
        base: base(),
        command_session_id: "cmd-1".parse().expect("command session id"),
    };

    cancel_command_session(&transport, &sandbox_id(), &request)
        .await
        .expect("cancel command");

    let calls = transport.calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].op, DaemonOp::CommandCancel);
    assert_eq!(calls[0].payload["caller_id"], json!("agent-1"));
    assert_eq!(calls[0].payload["command_session_id"], json!("cmd-1"));
}

#[tokio::test]
async fn collect_command_completions_drops_non_object_entries() {
    let transport = RecordingTransport::ok(json!({
        "completions": [
            {"command_session_id": "cmd-1", "status": "completed"},
            "not-an-object",
            {"command_session_id": "cmd-2", "status": "error"}
        ]
    }));

    let completions = collect_command_completions(
        &transport,
        &sandbox_id(),
        "agent-1",
        &["cmd-1".to_owned(), "cmd-2".to_owned()],
    )
    .await
    .expect("collect completions");

    assert_eq!(completions.len(), 2);
    assert_eq!(completions[0]["command_session_id"], json!("cmd-1"));
    assert_eq!(completions[1]["command_session_id"], json!("cmd-2"));
    let calls = transport.calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].op, DaemonOp::CommandCollectCompleted);
    assert_eq!(calls[0].payload["caller_id"], json!("agent-1"));
    assert_eq!(
        calls[0].payload["command_session_ids"],
        json!(["cmd-1", "cmd-2"])
    );
}
