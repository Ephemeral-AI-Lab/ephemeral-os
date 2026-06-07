use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use eos_sandbox_port::{DaemonOp, SandboxPortError};
use eos_types::JsonObject;
use serde_json::{json, Value};

use super::super::exec_command::ExecCommand;
use super::super::read_file::ReadFile;
use super::super::read_command_progress::ReadCommandProgress;
use super::super::write_stdin::WriteStdin;
use crate::core::metadata::ExecutionMetadata;
use crate::runtime::executor::ToolExecutor;
use crate::support::{test_agent_run_id, FakeTransport};
use crate::tools::{CommandToolService, SandboxToolService};

fn metadata() -> ExecutionMetadata {
    let agent_run_id = test_agent_run_id();
    ExecutionMetadata {
        agent_name: "tester".to_owned(),
        agent_run_id: Some(agent_run_id),
        request_id: None,
        task_id: None,
        attempt_id: None,
        workflow_id: None,
        tool_use_id: None,
        sandbox_invocation_id: Some("inv-1".parse().expect("id")),
        sandbox_id: Some("sandbox-1".parse().expect("id")),
        is_isolated_workspace_mode: false,
        workspace_root: "/repo".to_owned(),
        conversation: Arc::from(Vec::new()),
    }
}

fn sandbox_service(transport: Arc<dyn eos_sandbox_port::SandboxTransport>) -> SandboxToolService {
    SandboxToolService::new(transport)
}

fn command_service(transport: Arc<dyn eos_sandbox_port::SandboxTransport>) -> CommandToolService {
    CommandToolService::new(transport, None)
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
    let tool = ExecCommand::new(command_service(transport));
    let ctx = metadata();
    let input = obj(&[("cmd", json!("sleep 5"))]);
    let res = tool.execute(&input, &ctx).await.expect("ok");
    assert!(!res.is_error);
    assert_eq!(res.metadata["command_session_id"], json!("cs-7"));
    let payload: serde_json::Value = serde_json::from_str(&res.output).expect("json");
    assert_eq!(payload["command_session_id"], json!("cs-7"));
}

#[tokio::test]
async fn exec_command_rejects_invalid_numeric_bounds() {
    let transport = Arc::new(FakeTransport::inert());
    let tool = ExecCommand::new(command_service(transport));
    let ctx = metadata();
    for input in [
        obj(&[("cmd", json!("true")), ("yield_time_ms", json!(30_001))]),
        obj(&[("cmd", json!("true")), ("timeout", json!(0))]),
        obj(&[("cmd", json!("true")), ("max_output_tokens", json!(0))]),
    ] {
        let res = tool.execute(&input, &ctx).await.expect("ok");
        assert!(res.is_error, "{}", res.output);
        assert!(res.output.contains("Invalid input for exec_command"));
    }
}

#[tokio::test]
async fn write_stdin_ctrl_c_uses_command_cancel() {
    let cancels = Arc::new(AtomicUsize::new(0));
    let cancels_seen = cancels.clone();
    let transport = Arc::new(FakeTransport::new(move |op, _| match op {
        DaemonOp::CommandCancel => {
            cancels_seen.fetch_add(1, Ordering::SeqCst);
            Ok(obj(&[
                ("status", json!("cancelled")),
                ("exit_code", json!(130)),
                ("output", json!({"stdout": "", "stderr": ""})),
            ]))
        }
        other => Err(SandboxPortError::decode(format!("unexpected op {other:?}"))),
    }));
    let tool = WriteStdin::new(command_service(transport));
    let ctx = metadata();
    let input = obj(&[
        ("command_session_id", json!("cs-7")),
        ("chars", json!("\u{3}")),
    ]);
    let res = tool.execute(&input, &ctx).await.expect("ok");
    assert_eq!(
        cancels.load(Ordering::SeqCst),
        1,
        "ctrl-c must issue a command-session cancel RPC"
    );
    let payload: serde_json::Value = serde_json::from_str(&res.output).expect("json");
    assert_eq!(payload["status"], json!("cancelled"));
}

#[tokio::test]
async fn write_stdin_ctrl_d_uses_command_cancel() {
    let cancels = Arc::new(AtomicUsize::new(0));
    let cancels_seen = cancels.clone();
    let transport = Arc::new(FakeTransport::new(move |op, payload| match op {
        DaemonOp::CommandCancel => {
            assert_eq!(payload["command_session_id"], json!("cs-7"));
            cancels_seen.fetch_add(1, Ordering::SeqCst);
            Ok(obj(&[
                ("status", json!("cancelled")),
                ("exit_code", json!(130)),
                ("output", json!({"stdout": "", "stderr": ""})),
            ]))
        }
        other => Err(SandboxPortError::decode(format!("unexpected op {other:?}"))),
    }));
    let tool = WriteStdin::new(command_service(transport));
    let ctx = metadata();
    let input = obj(&[
        ("command_session_id", json!("cs-7")),
        ("chars", json!("\u{4}")),
    ]);
    let res = tool.execute(&input, &ctx).await.expect("ok");
    assert_eq!(
        cancels.load(Ordering::SeqCst),
        1,
        "ctrl-d must issue a command-session cancel RPC"
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
            ("output", json!({"stdout": "", "stderr": ""})),
        ])),
        other => Err(SandboxPortError::decode(format!("unexpected op {other:?}"))),
    }));
    let tool = WriteStdin::new(command_service(transport));
    let ctx = metadata();
    let input = obj(&[
        ("command_session_id", json!("cs-7")),
        ("chars", json!("y\n")),
    ]);
    let res = tool.execute(&input, &ctx).await.expect("ok");
    let payload: serde_json::Value = serde_json::from_str(&res.output).expect("json");
    assert_eq!(payload["status"], json!("running"));
}

#[tokio::test]
async fn write_stdin_rejects_invalid_inputs() {
    let transport = Arc::new(FakeTransport::inert());
    let tool = WriteStdin::new(command_service(transport));
    let ctx = metadata();
    for input in [
        obj(&[
            ("command_session_id", json!("cs-7")),
            ("chars", json!("")),
        ]),
        obj(&[
            ("command_session_id", json!("cs-7")),
            ("chars", json!("a\u{3}")),
        ]),
        obj(&[("command_session_id", json!("")), ("chars", json!("x"))]),
    ] {
        let res = tool.execute(&input, &ctx).await.expect("ok");
        assert!(res.is_error, "{}", res.output);
        assert!(res.output.contains("Invalid input for write_stdin"));
    }
}

#[tokio::test]
async fn read_command_progress_uses_tail_snapshot_op() {
    let transport = Arc::new(FakeTransport::new(|op, payload| match op {
        DaemonOp::CommandReadProgress => {
            assert_eq!(payload["command_session_id"], json!("cs-7"));
            assert_eq!(payload["last_n_lines"], json!(25));
            Ok(obj(&[
                ("status", json!("running")),
                ("command_session_id", json!("cs-7")),
                ("output", json!({"stdout": "tail\n", "stderr": ""})),
            ]))
        }
        other => Err(SandboxPortError::decode(format!("unexpected op {other:?}"))),
    }));
    let tool = ReadCommandProgress::new(command_service(transport));
    let ctx = metadata();
    let input = obj(&[
        ("command_session_id", json!("cs-7")),
        ("last_n_lines", json!(25)),
    ]);
    let res = tool.execute(&input, &ctx).await.expect("ok");
    let payload: serde_json::Value = serde_json::from_str(&res.output).expect("json");
    assert_eq!(payload["status"], json!("running"));
    assert_eq!(payload["stdout"], json!("tail\n"));
}

#[tokio::test]
async fn read_command_progress_rejects_invalid_bounds() {
    let transport = Arc::new(FakeTransport::inert());
    let tool = ReadCommandProgress::new(command_service(transport));
    let ctx = metadata();
    for input in [
        obj(&[("command_session_id", json!("cs-7")), ("last_n_lines", json!(0))]),
        obj(&[("command_session_id", json!("cs-7")), ("last_n_lines", json!(201))]),
        obj(&[("command_session_id", json!("")), ("last_n_lines", json!(1))]),
    ] {
        let res = tool.execute(&input, &ctx).await.expect("ok");
        assert!(res.is_error, "{}", res.output);
        assert!(res.output.contains("Invalid input for read_command_progress"));
    }
}

#[tokio::test]
async fn read_file_rejects_zero_line_numbers() {
    let transport = Arc::new(FakeTransport::inert());
    let tool = ReadFile::new(sandbox_service(transport));
    let ctx = metadata();
    for input in [
        obj(&[("file_path", json!("src/lib.rs")), ("start_line", json!(0))]),
        obj(&[
            ("file_path", json!("src/lib.rs")),
            ("start_line", json!(1)),
            ("end_line", json!(0)),
        ]),
    ] {
        let res = tool.execute(&input, &ctx).await.expect("ok");
        assert!(res.is_error, "{}", res.output);
        assert!(res.output.contains("Invalid input for read_file"));
    }
}
