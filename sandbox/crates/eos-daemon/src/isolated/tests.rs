use super::*;

type TestResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn active_command_session_records_do_not_guard_exit() -> TestResult {
    let _guard = TEST_LOCK.lock().map_err(|_| "test lock poisoned")?;
    let _ = op_test_reset(&json!({}), DispatchContext::empty());
    let root = std::env::temp_dir().join(format!(
        "eos-daemon-iws-command-session-block-{}",
        std::process::id()
    ));
    let scratch = root.join("scratch");
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(root.join("layers"))?;
    std::fs::create_dir_all(root.join("staging"))?;
    std::fs::write(
        root.join("manifest.json"),
        r#"{"schema_version":1,"version":1,"layers":[]}"#,
    )?;
    set_env("EOS_ISOLATED_WORKSPACE_ENABLED", "true");
    set_env(TEST_HARNESS_ENV, "true");
    set_env(TEST_SCRATCH_ROOT_ENV, &scratch.to_string_lossy());

    let entered = op_enter(
        &json!({"agent_id": "agent-command-session", "layer_stack_root": root}),
        DispatchContext::empty(),
    )?;
    assert_eq!(entered["success"], true);
    register_command_session("agent-command-session", "cmd-block");

    let exited = op_exit(
        &json!({"agent_id": "agent-command-session"}),
        DispatchContext::empty(),
    )?;
    assert_eq!(exited["success"], true);
    assert_eq!(
        exited["inspection"]["handle_registered_after"],
        json!(false)
    );
    let _ = op_test_reset(&json!({}), DispatchContext::empty());
    clear_env("EOS_ISOLATED_WORKSPACE_ENABLED");
    clear_env(TEST_HARNESS_ENV);
    clear_env(TEST_SCRATCH_ROOT_ENV);
    let _ = std::fs::remove_dir_all(&root);
    Ok(())
}

#[test]
fn enter_uses_workspace_binding_over_eos_workspace_root_env() -> TestResult {
    let _guard = TEST_LOCK.lock().map_err(|_| "test lock poisoned")?;
    let root = std::env::temp_dir().join(format!(
        "eos-daemon-iws-bound-workspace-root-{}",
        std::process::id()
    ));
    let scratch = root.join("scratch");
    let stack_root = root.join("stack");
    let workspace_root = root.join("workspace");
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&workspace_root)?;
    std::fs::write(workspace_root.join("seed.txt"), "seed\n")?;
    eos_layerstack::build_workspace_base(&stack_root, &workspace_root, true)?;
    set_env("EOS_ISOLATED_WORKSPACE_ENABLED", "true");
    set_env(TEST_HARNESS_ENV, "true");
    set_env(TEST_SCRATCH_ROOT_ENV, &scratch.to_string_lossy());
    set_env("EOS_WORKSPACE_ROOT", "/configured-fallback");
    let _ = op_test_reset(&json!({}), DispatchContext::empty());

    let entered = op_enter(
        &json!({"agent_id": "agent-bound-root", "layer_stack_root": stack_root}),
        DispatchContext::empty(),
    )?;

    assert_eq!(entered["success"], true);
    let expected_workspace_root = workspace_root.to_string_lossy().into_owned();
    assert_eq!(
        entered["workspace_root"],
        json!(expected_workspace_root.clone())
    );
    let status = op_status(
        &json!({"agent_id": "agent-bound-root"}),
        DispatchContext::empty(),
    )?;
    assert_eq!(status["success"], true);
    assert_eq!(status["open"], true);
    assert_eq!(
        status["workspace_root"],
        json!(expected_workspace_root.clone())
    );

    let exited = op_exit(
        &json!({"agent_id": "agent-bound-root"}),
        DispatchContext::empty(),
    )?;
    assert_eq!(exited["success"], true);
    let _ = op_test_reset(&json!({}), DispatchContext::empty());
    clear_env("EOS_WORKSPACE_ROOT");
    clear_env("EOS_ISOLATED_WORKSPACE_ENABLED");
    clear_env(TEST_HARNESS_ENV);
    clear_env(TEST_SCRATCH_ROOT_ENV);
    let _ = std::fs::remove_dir_all(&root);
    Ok(())
}

#[test]
fn test_reset_rewrites_invalid_manager_json() -> TestResult {
    let _guard = TEST_LOCK.lock().map_err(|_| "test lock poisoned")?;
    let root = std::env::temp_dir().join(format!(
        "eos-daemon-iws-reset-manager-{}",
        std::process::id()
    ));
    let scratch = root.join("scratch");
    let manager_root = scratch.clone();
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&manager_root)?;
    std::fs::write(
        manager_root.join("manager.json"),
        r#"{"schema_version":999,"handles":[{"workspace_handle_id":"ghost"}]}"#,
    )?;
    set_env(TEST_HARNESS_ENV, "true");
    set_env(TEST_SCRATCH_ROOT_ENV, &scratch.to_string_lossy());

    let reset = op_test_reset(&json!({}), DispatchContext::empty())?;

    assert_eq!(reset["success"], true);
    let rewritten = std::fs::read_to_string(manager_root.join("manager.json"))?;
    assert_eq!(
        serde_json::from_str::<Value>(&rewritten)?,
        json!({"schema_version": 1, "handles": []})
    );
    clear_env(TEST_HARNESS_ENV);
    clear_env(TEST_SCRATCH_ROOT_ENV);
    let _ = std::fs::remove_dir_all(&root);
    Ok(())
}

#[test]
fn host_ram_pressure_error_keeps_capacity_details() {
    let response = error_payload(&IsolatedError::HostRamPressure {
        required_bytes: 30,
        budget_bytes: 29,
    });
    assert_eq!(response["success"], false);
    assert_eq!(response["error"]["kind"], "host_ram_pressure");
    assert_eq!(response["error"]["details"]["required_bytes"], 30);
    assert_eq!(response["error"]["details"]["budget_bytes"], 29);
}

static TEST_LOCK: Mutex<()> = Mutex::new(());

fn set_env(key: &str, value: &str) {
    std::env::set_var(key, value);
}

fn clear_env(key: &str) {
    std::env::remove_var(key);
}
