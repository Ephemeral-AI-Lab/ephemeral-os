use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use eos_sandbox_api::{DaemonOp, SandboxApiError};
use eos_skills::SkillRegistry;
use eos_types::JsonObject;
use serde_json::{json, Value};

use super::super::exec_command::ExecCommand;
use super::super::read_file::ReadFile;
use super::super::write_stdin::WriteStdin;
use crate::core::metadata::ExecutionMetadata;
use crate::runtime::executor::ToolExecutor;
use crate::testsupport::{test_agent_run_id, FakeRequestStore, FakeTaskStore, FakeTransport};

fn metadata_with(transport: Arc<dyn eos_sandbox_api::SandboxTransport>) -> ExecutionMetadata {
    let agent_run_id = test_agent_run_id();
    ExecutionMetadata {
        sandbox_id: Some("sandbox-1".parse().expect("id")),
        agent_run_id: Some(agent_run_id),
        agent_name: "tester".to_owned(),
        cwd: String::new(),
        repo_root: "/repo".to_owned(),
        exec_cwd: String::new(),
        request_id: None,
        task_id: None,
        attempt_id: None,
        workflow_id: None,
        tool_use_id: None,
        sandbox_invocation_id: Some("inv-1".parse().expect("id")),
        transport,
        task_store: Arc::new(FakeTaskStore::new()),
        request_store: Arc::new(FakeRequestStore::new()),
        skill_registry: Arc::new(SkillRegistry::new()),
        workflow_control: None,
        plan_submission: None,
        background_supervisor: None,
        command_session_supervisor: None,
        isolated_workspace: None,
        notifications: None,
        conversation: Arc::from(Vec::new()),
    }
}

fn obj(pairs: &[(&str, Value)]) -> JsonObject {
    pairs
        .iter()
        .map(|(k, v)| ((*k).to_owned(), v.clone()))
        .collect()
}

// AC-tools-11 (exec_command half): exec_command surfaces command_session_id
// from the daemon response.
#[tokio::test]
async fn exec_command_session() {
    let transport = Arc::new(FakeTransport::new(|op, _| {
        assert_eq!(op, DaemonOp::ExecCommand);
        Ok(obj(&[
            ("status", json!("running")),
            ("command_session_id", json!("cs-7")),
            ("output", json!({"stdout": "", "stderr": ""})),
        ]))
    }));
    let ctx = metadata_with(transport);
    let input = obj(&[("cmd", json!("sleep 5"))]);
    let res = ExecCommand.execute(&input, &ctx).await.expect("ok");
    assert!(!res.is_error);
    assert_eq!(res.metadata["command_session_id"], json!("cs-7"));
    let payload: serde_json::Value = serde_json::from_str(&res.output).expect("json");
    assert_eq!(payload["command_session_id"], json!("cs-7"));
}

#[tokio::test]
async fn exec_command_rejects_invalid_numeric_bounds() {
    let ctx = metadata_with(Arc::new(FakeTransport::inert()));
    for input in [
        obj(&[("cmd", json!("true")), ("yield_time_ms", json!(30_001))]),
        obj(&[("cmd", json!("true")), ("timeout", json!(0))]),
        obj(&[("cmd", json!("true")), ("max_output_tokens", json!(0))]),
    ] {
        let res = ExecCommand.execute(&input, &ctx).await.expect("ok");
        assert!(res.is_error, "{}", res.output);
        assert!(res.output.contains("Invalid input for exec_command"));
    }
}

// sense-2 D7: `\x03` is SIGINT-only and rides through as ordinary stdin — the
// tool no longer escalates to a cancel RPC (the daemon raises SIGINT itself).
#[tokio::test]
async fn write_stdin_ctrl_c_does_not_escalate_to_cancel() {
    let cancels = Arc::new(AtomicUsize::new(0));
    let cancels_seen = cancels.clone();
    let transport = Arc::new(FakeTransport::new(move |op, _| match op {
        DaemonOp::ExecStdin => Ok(obj(&[
            ("status", json!("running")),
            ("output", json!({"stdout": "", "stderr": ""})),
        ])),
        DaemonOp::CommandCancel => {
            cancels_seen.fetch_add(1, Ordering::SeqCst);
            Ok(obj(&[("status", json!("cancelled"))]))
        }
        other => Err(SandboxApiError::decode(format!("unexpected op {other:?}"))),
    }));
    let ctx = metadata_with(transport);
    let input = obj(&[
        ("command_session_id", json!("cs-7")),
        ("chars", json!("\u{3}")),
    ]);
    let res = WriteStdin.execute(&input, &ctx).await.expect("ok");
    assert_eq!(
        cancels.load(Ordering::SeqCst),
        0,
        "ctrl-c must NOT issue a cancel RPC (D7: SIGINT only)"
    );
    let payload: serde_json::Value = serde_json::from_str(&res.output).expect("json");
    assert_eq!(payload["status"], json!("running"));
}

// sense-2 D7: `terminate: true` is forwarded on the write RPC so the daemon
// tears the session down; no separate cancel RPC is issued by the tool.
#[tokio::test]
async fn write_stdin_terminate_forwards_flag() {
    let terminate_seen = Arc::new(AtomicUsize::new(0));
    let seen = terminate_seen.clone();
    let transport = Arc::new(FakeTransport::new(move |op, payload| match op {
        DaemonOp::ExecStdin => {
            if payload.get("terminate").and_then(Value::as_bool) == Some(true) {
                seen.fetch_add(1, Ordering::SeqCst);
            }
            Ok(obj(&[
                ("status", json!("cancelled")),
                ("exit_code", json!(130)),
                ("output", json!({"stdout": "", "stderr": ""})),
            ]))
        }
        other => Err(SandboxApiError::decode(format!("unexpected op {other:?}"))),
    }));
    let ctx = metadata_with(transport);
    let input = obj(&[
        ("command_session_id", json!("cs-7")),
        ("terminate", json!(true)),
    ]);
    let res = WriteStdin.execute(&input, &ctx).await.expect("ok");
    assert_eq!(
        terminate_seen.load(Ordering::SeqCst),
        1,
        "the terminate flag must be forwarded on the write RPC"
    );
    let payload: serde_json::Value = serde_json::from_str(&res.output).expect("json");
    assert_eq!(payload["status"], json!("cancelled"));
}

// A non-ctrl-c write does not cancel.
#[tokio::test]
async fn write_stdin_plain_does_not_cancel() {
    let transport = Arc::new(FakeTransport::new(|op, _| match op {
        DaemonOp::ExecStdin => Ok(obj(&[
            ("status", json!("running")),
            ("output", json!({"stdout": "ok", "stderr": ""})),
        ])),
        other => Err(SandboxApiError::decode(format!("unexpected op {other:?}"))),
    }));
    let ctx = metadata_with(transport);
    let input = obj(&[
        ("command_session_id", json!("cs-7")),
        ("chars", json!("y\n")),
    ]);
    let res = WriteStdin.execute(&input, &ctx).await.expect("ok");
    let payload: serde_json::Value = serde_json::from_str(&res.output).expect("json");
    assert_eq!(payload["status"], json!("running"));
}

#[tokio::test]
async fn write_stdin_rejects_invalid_numeric_bounds() {
    let ctx = metadata_with(Arc::new(FakeTransport::inert()));
    for input in [
        obj(&[
            ("command_session_id", json!("cs-7")),
            ("yield_time_ms", json!(30_001)),
        ]),
        obj(&[
            ("command_session_id", json!("cs-7")),
            ("max_output_tokens", json!(0)),
        ]),
        obj(&[("command_session_id", json!(""))]),
    ] {
        let res = WriteStdin.execute(&input, &ctx).await.expect("ok");
        assert!(res.is_error, "{}", res.output);
        assert!(res.output.contains("Invalid input for write_stdin"));
    }
}

#[tokio::test]
async fn read_file_rejects_zero_line_numbers() {
    let ctx = metadata_with(Arc::new(FakeTransport::inert()));
    for input in [
        obj(&[("file_path", json!("src/lib.rs")), ("start_line", json!(0))]),
        obj(&[
            ("file_path", json!("src/lib.rs")),
            ("start_line", json!(1)),
            ("end_line", json!(0)),
        ]),
    ] {
        let res = ReadFile.execute(&input, &ctx).await.expect("ok");
        assert!(res.is_error, "{}", res.output);
        assert!(res.output.contains("Invalid input for read_file"));
    }
}
