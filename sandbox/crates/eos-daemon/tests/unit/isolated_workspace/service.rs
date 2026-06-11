use super::*;

use crate::runtime::services::Services;
use eos_config::configs::daemon::PluginRuntimeConfig;
use eos_config::configs::isolated_workspace::IsolatedWorkspaceConfig;

type TestResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn exit_tears_down_caller_handle() -> TestResult {
    // op_exit is the per-caller workspace-run teardown: it discards the
    // caller's command sessions (owned by the command-session registry now,
    // not an isolated side-map) and removes the handle.
    let _guard = lock_isolated_test_state();
    let root = std::env::temp_dir().join(format!(
        "eos-daemon-iws-command-session-block-{}",
        std::process::id()
    ));
    let scratch = root.join("scratch");
    let services = isolated_test_services(&scratch, Path::new("/testbed"));
    let context = DispatchContext::with_services(&services);
    set_env(TEST_HARNESS_ENV, "true");
    let _ = op_test_reset(&json!({}), context);
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(root.join("layers"))?;
    std::fs::create_dir_all(root.join("staging"))?;
    std::fs::write(
        root.join("manifest.json"),
        r#"{"schema_version":1,"version":1,"layers":[]}"#,
    )?;

    let entered = op_enter(
        &json!({"caller_id": "caller-command-session", "layer_stack_root": root}),
        context,
    )?;
    assert_eq!(entered["success"], true);

    let exited = op_exit(&json!({"caller_id": "caller-command-session"}), context)?;
    assert_eq!(exited["success"], true);
    assert_eq!(
        exited["inspection"]["handle_registered_after"],
        json!(false)
    );
    let _ = op_test_reset(&json!({}), context);
    clear_env(TEST_HARNESS_ENV);
    let _ = std::fs::remove_dir_all(&root);
    Ok(())
}

#[test]
fn enter_uses_workspace_binding_over_configured_workspace_root() -> TestResult {
    let _guard = lock_isolated_test_state();
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
    let services = isolated_test_services(&scratch, Path::new("/configured-fallback"));
    let context = DispatchContext::with_services(&services);
    set_env(TEST_HARNESS_ENV, "true");
    let _ = op_test_reset(&json!({}), context);

    let entered = op_enter(
        &json!({"caller_id": "caller-bound-root", "layer_stack_root": stack_root}),
        context,
    )?;

    assert_eq!(entered["success"], true);
    let expected_workspace_root = workspace_root.to_string_lossy().into_owned();
    assert_eq!(
        entered["workspace_root"],
        json!(expected_workspace_root.clone())
    );
    let status = op_status(&json!({"caller_id": "caller-bound-root"}), context)?;
    assert_eq!(status["success"], true);
    assert_eq!(status["open"], true);
    assert_eq!(
        status["workspace_root"],
        json!(expected_workspace_root.clone())
    );

    let exited = op_exit(&json!({"caller_id": "caller-bound-root"}), context)?;
    assert_eq!(exited["success"], true);
    let _ = op_test_reset(&json!({}), context);
    clear_env(TEST_HARNESS_ENV);
    let _ = std::fs::remove_dir_all(&root);
    Ok(())
}

#[test]
fn enter_rebinds_idle_state_to_new_layer_stack_root() -> TestResult {
    let _guard = lock_isolated_test_state();
    let root =
        std::env::temp_dir().join(format!("eos-daemon-iws-root-switch-{}", std::process::id()));
    let scratch = root.join("scratch");
    let stack_a = root.join("stack-a");
    let stack_b = root.join("stack-b");
    let services = isolated_test_services(&scratch, Path::new("/testbed"));
    let context = DispatchContext::with_services(&services);
    set_env(TEST_HARNESS_ENV, "true");
    let _ = op_test_reset(&json!({}), context);
    let _ = std::fs::remove_dir_all(&root);
    seed_empty_stack(&stack_a)?;
    seed_empty_stack(&stack_b)?;

    let entered_a = op_enter(
        &json!({"caller_id": "caller-root-a", "layer_stack_root": stack_a}),
        context,
    )?;
    assert_eq!(entered_a["success"], true);
    assert_eq!(
        eos_layerstack::LayerStack::open(stack_a.clone())?.active_lease_count(),
        1
    );
    assert_eq!(
        eos_layerstack::LayerStack::open(stack_b.clone())?.active_lease_count(),
        0
    );
    let exited_a = op_exit(&json!({"caller_id": "caller-root-a"}), context)?;
    assert_eq!(exited_a["success"], true);

    let entered_b = op_enter(
        &json!({"caller_id": "caller-root-b", "layer_stack_root": stack_b}),
        context,
    )?;
    assert_eq!(entered_b["success"], true);
    assert_eq!(
        eos_layerstack::LayerStack::open(stack_a.clone())?.active_lease_count(),
        0
    );
    assert_eq!(
        eos_layerstack::LayerStack::open(stack_b.clone())?.active_lease_count(),
        1
    );

    let exited_b = op_exit(&json!({"caller_id": "caller-root-b"}), context)?;
    assert_eq!(exited_b["success"], true);
    let _ = op_test_reset(&json!({}), context);
    clear_env(TEST_HARNESS_ENV);
    let _ = std::fs::remove_dir_all(&root);
    Ok(())
}

#[test]
fn test_reset_rewrites_invalid_manager_json() -> TestResult {
    let _guard = lock_isolated_test_state();
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
    let services = isolated_test_services(&scratch, Path::new("/testbed"));
    let context = DispatchContext::with_services(&services);
    set_env(TEST_HARNESS_ENV, "true");

    let reset = op_test_reset(&json!({}), context)?;

    assert_eq!(reset["success"], true);
    let rewritten = std::fs::read_to_string(manager_root.join("manager.json"))?;
    assert_eq!(
        serde_json::from_str::<Value>(&rewritten)?,
        json!({"schema_version": 1, "handles": []})
    );
    clear_env(TEST_HARNESS_ENV);
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

fn set_env(key: &str, value: &str) {
    std::env::set_var(key, value);
}

fn clear_env(key: &str) {
    std::env::remove_var(key);
}

fn isolated_test_services(scratch_root: &Path, workspace_root: &Path) -> Services {
    Services::new(
        PluginRuntimeConfig::default(),
        IsolatedWorkspaceConfig {
            enabled: true,
            scratch_root: scratch_root.to_path_buf(),
            workspace_root: workspace_root.to_path_buf(),
            ..IsolatedWorkspaceConfig::default()
        },
    )
}

fn seed_empty_stack(root: &Path) -> TestResult {
    std::fs::create_dir_all(root.join("layers"))?;
    std::fs::create_dir_all(root.join("staging"))?;
    std::fs::write(
        root.join("manifest.json"),
        r#"{"schema_version":1,"version":1,"layers":[]}"#,
    )?;
    Ok(())
}
