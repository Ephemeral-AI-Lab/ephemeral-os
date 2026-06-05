use serde_json::json;

use super::session::should_publish_command_session_completion;
#[cfg(target_os = "linux")]
use super::session::CommandSessionRegistry;
use super::*;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn exec_command_requires_string_wire_shape() {
    assert!(require_command_string(&json!({"cmd": "echo hi"}), "cmd").is_ok());
    assert!(require_command_string(&json!({"cmd": ["true"]}), "cmd").is_err());
}

#[test]
fn exec_command_preserves_shell_string_bytes_after_validation() -> TestResult {
    assert_eq!(
        require_command_string(&json!({"cmd": "  printf hi\n"}), "cmd")?,
        "  printf hi\n"
    );
    Ok(())
}

#[test]
fn optional_u64_accepts_unsigned_and_nonnegative_signed_numbers() {
    assert_eq!(optional_u64(&json!({"timeout": 7_u64}), "timeout"), Some(7));
    assert_eq!(optional_u64(&json!({"timeout": 7_i64}), "timeout"), Some(7));
    assert_eq!(optional_u64(&json!({"timeout": -1_i64}), "timeout"), None);
}

#[test]
fn command_session_cancel_suppresses_background_completion_publication() {
    assert!(should_publish_command_session_completion(true, false, true));
    assert!(!should_publish_command_session_completion(true, true, true));
    assert!(!should_publish_command_session_completion(
        true, false, false
    ));
    assert!(!should_publish_command_session_completion(
        false, false, true
    ));
    assert!(!should_publish_command_session_completion(
        false, true, false
    ));
}

#[test]
#[cfg(target_os = "linux")]
fn command_session_completion_result_can_be_claimed_by_control_tool() -> TestResult {
    let registry = CommandSessionRegistry::new();
    registry.push_completed(json!({
        "command_session_id": "cmd_keep",
        "result": {"status": "ok", "exit_code": 0},
    }));
    registry.push_completed(json!({
        "command_session_id": "cmd_done",
        "result": {"status": "ok", "exit_code": 0},
    }));

    let result = registry
        .take_completed_result("cmd_done")
        .ok_or("matching completion should be returned")?;
    assert_eq!(result["status"], "ok");
    assert!(registry.take_completed_result("cmd_done").is_none());

    let remaining = registry.collect_completed(&json!({"command_session_ids": ["cmd_keep"]}));
    assert_eq!(
        remaining["completions"]
            .as_array()
            .ok_or("completions should be an array")?
            .len(),
        1
    );

    // Remove-on-deliver: a second collect finds nothing — the map is bounded,
    // not accumulating delivered entries forever.
    let redelivered = registry.collect_completed(&json!({"command_session_ids": ["cmd_keep"]}));
    assert_eq!(
        redelivered["completions"]
            .as_array()
            .ok_or("completions should be an array")?
            .len(),
        0
    );
    Ok(())
}

/// A minimal live `CommandSession` for registry/count tests. The workspace is
/// an empty isolated stub (never finalized here), so only `id`/`agent_id`
/// matter. One constructor keeps the 16-field literal in a single place.
#[cfg(target_os = "linux")]
fn test_command_session(id: &str, agent_id: &str) -> TestResult<CommandSession> {
    let writer = Mutex::new(
        OpenOptions::new()
            .read(true)
            .write(true)
            .open("/dev/null")?,
    );
    Ok(CommandSession {
        id: id.to_owned(),
        agent_id: agent_id.to_owned(),
        command: "test".to_owned(),
        started_at: Instant::now(),
        pgid: 0,
        writer,
        output: Arc::new(CommandSessionOutput::new()),
        reader_done: Mutex::new(None),
        cancelled: Mutex::new(false),
        interrupted: Mutex::new(false),
        model_cursor: Mutex::new(CommandSessionOutputCursor::default()),
        notification_cursor: Mutex::new(CommandSessionOutputCursor::default()),
        child: Mutex::new(None),
        workspace: CommandWorkspaceKind::Isolated(IsolatedCommandWorkspace {
            handle: crate::services::isolated_workspace::CommandHandle {
                agent_id: String::new(),
                workspace_handle_id: String::new(),
                layer_stack_root: PathBuf::new(),
                manifest_version: 0,
                manifest_root_hash: String::new(),
                workspace_root: PathBuf::new(),
                scratch_dir: PathBuf::new(),
                upperdir: PathBuf::new(),
                workdir: PathBuf::new(),
                layer_paths: Vec::new(),
                ns_fds: HashMap::new(),
                cgroup_path: None,
            },
            output_path: PathBuf::new(),
            final_path: PathBuf::new(),
        }),
        finalized: Mutex::new(None),
        timeout_deadline: None,
    })
}

#[test]
#[cfg(target_os = "linux")]
fn command_session_count_counts_live_sessions_by_agent() -> TestResult {
    let registry = CommandSessionRegistry::new();
    registry.insert(Arc::new(test_command_session("cmd_a", "agent-a")?));
    registry.insert(Arc::new(test_command_session("cmd_b", "agent-b")?));

    assert_eq!(registry.count_by_agent("agent-a"), 1);
    assert_eq!(registry.count_by_agent("agent-b"), 1);
    assert_eq!(registry.count_by_agent(""), 2);
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn command_session_write_stdin_returns_completed_result_when_live_session_is_gone() -> TestResult {
    let id = "cmd_stdin_done_unit";
    command_session_registry().push_completed(json!({
        "command_session_id": id,
        "result": {
            "status": "ok",
            "exit_code": 0,
            "output": {"stdout": "written\n", "stderr": ""},
        },
    }));

    let response =
        command_session_write_stdin(&json!({"command_session_id": id, "chars": "ignored"}))?;

    assert_eq!(response["status"], "ok");
    assert_eq!(response["output"]["stdout"], "written\n");
    assert!(command_session_registry()
        .take_completed_result(id)
        .is_none());
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn command_session_cancel_returns_completed_result_when_live_session_is_gone() -> TestResult {
    let id = "command_session_cancel_done_unit";
    command_session_registry().push_completed(json!({
        "command_session_id": id,
        "result": {
            "status": "ok",
            "exit_code": 0,
            "output": {"stdout": "already-finished\n", "stderr": ""},
        },
    }));

    let response = command_session_cancel(&json!({"command_session_id": id}))?;

    assert_eq!(response["status"], "ok");
    assert_eq!(response["output"]["stdout"], "already-finished\n");
    assert!(command_session_registry()
        .take_completed_result(id)
        .is_none());
    Ok(())
}
