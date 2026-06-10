use std::future;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};
use std::thread;
use std::time::Duration;

use crate::audit::schema::Lane;
use serde_json::json;

use super::*;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn upperdir_tree_resource_timings_capture_bounded_payload() -> TestResult {
    let fixture = Fixture::new("upperdir_tree_stats")?;
    let upperdir = fixture.base.join("upperdir");
    std::fs::create_dir_all(upperdir.join("nested"))?;
    std::fs::write(upperdir.join("nested/payload.bin"), vec![7_u8; 4096])?;

    let manifest = LayerStack::open(fixture.root.clone())?.read_active_manifest()?;
    let mut timings = resource_timings(&manifest, 1);
    let upperdir_stats = eos_workspace_runtime::ephemeral::TreeResourceStats::collect(&upperdir);
    insert_tree_resource_timings(
        &mut timings,
        "resource.command_exec.upperdir",
        &TreeResourceStats::from_ephemeral(&upperdir_stats),
    );

    assert_eq!(
        timing_f64_value(&timings, "resource.command_exec.workspace_tree_bytes"),
        0.0
    );
    assert_eq!(
        timing_f64_value(&timings, "resource.command_exec.upperdir_tree_exists"),
        1.0
    );
    assert!(timing_f64_value(&timings, "resource.command_exec.upperdir_tree_bytes") >= 4096.0);
    assert_eq!(
        timing_f64_value(&timings, "resource.command_exec.upperdir_tree_truncated"),
        0.0
    );
    Ok(())
}

#[test]
fn op_table_rejects_different_handler_collision() {
    #[expect(
        clippy::unnecessary_wraps,
        reason = "test handlers must match the dispatcher handler ABI"
    )]
    fn first_handler(_args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
        Ok(json!({"handler": "first"}))
    }
    #[expect(
        clippy::unnecessary_wraps,
        reason = "test handlers must match the dispatcher handler ABI"
    )]
    fn second_handler(_args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
        Ok(json!({"handler": "second"}))
    }

    let mut table = OpTable::default();
    assert!(table.register("api.test.collision", first_handler));
    assert!(table.register("api.test.collision", first_handler));
    assert!(!table.register("api.test.collision", second_handler));

    let response = table.dispatch(&Request {
        op: "api.test.collision".to_owned(),
        invocation_id: "collision-test".to_owned(),
        args: json!({}),
    });
    assert_eq!(response["handler"], "first");
}

#[test]
fn builtin_table_routes_commit_to_workspace() {
    let response = OpTable::with_builtins().dispatch(&Request {
        op: "api.commit_to_workspace".to_owned(),
        invocation_id: "commit-to-workspace-route-test".to_owned(),
        args: json!({}),
    });

    assert_ne!(response["error"]["kind"], json!("unknown_op"));
    assert_eq!(response["error"]["kind"], json!("invalid_envelope"));
    assert!(response["error"]["message"]
        .as_str()
        .unwrap_or_default()
        .contains("layer_stack_root is required"));
}

#[test]
fn builtin_table_routes_commit_to_git() {
    let response = OpTable::with_builtins().dispatch(&Request {
        op: "api.commit_to_git".to_owned(),
        invocation_id: "commit-to-git-route-test".to_owned(),
        args: json!({}),
    });

    assert_ne!(response["error"]["kind"], json!("unknown_op"));
    assert_eq!(response["error"]["kind"], json!("invalid_envelope"));
    assert!(response["error"]["message"]
        .as_str()
        .unwrap_or_default()
        .contains("layer_stack_root is required"));
}

#[test]
fn dispatch_attaches_real_runtime_timings() {
    #[expect(
        clippy::unnecessary_wraps,
        reason = "test handlers must match the dispatcher handler ABI"
    )]
    fn slow_handler(_args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
        std::thread::sleep(std::time::Duration::from_millis(2));
        Ok(json!({"success": true}))
    }

    let mut table = OpTable::default();
    assert!(table.register("api.test.slow", slow_handler));

    let response = table.dispatch_with_context(
        &Request {
            op: "api.test.slow".to_owned(),
            invocation_id: "timings-test".to_owned(),
            args: json!({}),
        },
        DispatchContext {
            invocation_registry: None,
            audit_config: None,
            file_limits: None,
            read_request_s: Some(0.125),
        },
    );

    assert_eq!(response["success"], json!(true));
    assert!(
        response["timings"]["runtime.boot_to_dispatch_s"]
            .as_f64()
            .unwrap_or_default()
            >= 0.0
    );
    assert!(
        response["timings"]["runtime.dispatch_s"]
            .as_f64()
            .unwrap_or_default()
            > 0.0
    );
    assert_eq!(response["timings"]["runtime.read_request_s"], json!(0.125));
}

#[tokio::test]
async fn cancel_waits_for_bounded_cleanup() -> TestResult {
    let registry = Arc::new(InFlightRegistry::new(300.0, 30.0));
    let task = tokio::spawn(future::pending::<()>());
    registry.register("cancel-target", task.abort_handle(), "caller-a", true);
    let cleanup_registry = Arc::clone(&registry);
    let cleanup_thread = thread::spawn(move || {
        thread::sleep(Duration::from_millis(20));
        cleanup_registry.deregister("cancel-target");
    });

    let response = OpTable::with_builtins().dispatch_with_context(
        &Request {
            op: "api.v1.cancel".to_owned(),
            invocation_id: "cancel-request".to_owned(),
            args: json!({"invocation_id": "cancel-target"}),
        },
        DispatchContext::with_invocation_registry(&registry),
    );

    cleanup_thread
        .join()
        .map_err(|_| "cleanup helper panicked")?;
    assert_eq!(response["cancelled"], json!(true));
    assert_eq!(response["already_done"], json!(false));
    assert_eq!(response["cleanup_done"], json!(true));
    match task.await {
        Ok(()) => Err("expected cancelled task".into()),
        Err(error) if error.is_cancelled() => Ok(()),
        Err(error) => Err(format!("expected cancellation, got {error}").into()),
    }
}

#[test]
fn internal_error_envelope_adds_error_id() {
    let response = error_envelope(
        ErrorKind::InternalError,
        "daemon invocation failed",
        json!({"op": "api.test.failure"}),
    );

    assert_eq!(response["error"]["kind"], json!("internal_error"));
    assert_eq!(
        response["error"]["details"]["op"],
        json!("api.test.failure")
    );
    let Some(error_id) = response["error"]["details"]["error_id"].as_str() else {
        panic!("internal errors carry details.error_id");
    };
    assert_eq!(error_id.len(), 32);
    assert!(error_id.bytes().all(|byte| byte.is_ascii_hexdigit()));
    assert_eq!(error_id.as_bytes()[12], b'4');
    assert!(matches!(error_id.as_bytes()[16], b'8' | b'9' | b'a' | b'b'));
}

#[test]
fn command_collect_completed_is_background_only_not_overlay_lifecycle() {
    let request = Request {
        op: "api.v1.command.collect_completed".to_owned(),
        invocation_id: "collect-completed".to_owned(),
        args: json!({"command_session_id": "cmd-1", "caller_id": "caller-1"}),
    };

    assert_eq!(
        background_event_kind(&request, &json!({"success": true})),
        Some(("background_tool.completed", "command_session"))
    );
    assert!(!uses_overlay_or_lease(
        &request.op,
        &json!({"success": true})
    ));
}

#[test]
fn audit_pull_reads_shared_daemon_ring() -> TestResult {
    let marker = format!("phase3t-audit-test-{}", unique_suffix());
    let after_seq = audit_after_seq()?;
    crate::audit::buffer::safe_emit(
        json!({"type": marker, "payload": {"source": "unit-test"}}),
        Lane::Normal,
    );

    let pulled = op_audit_pull(
        &json!({"after_seq": after_seq, "limit": 128}),
        DispatchContext::empty(),
    )?;

    let events = pulled["events"].as_array().ok_or("events array")?;
    assert!(events
        .iter()
        .any(|event| event["type"].as_str() == Some(marker.as_str())));
    Ok(())
}

#[test]
fn auto_squash_audit_emits_triggered_and_completed() -> TestResult {
    let fixture = Fixture::new("auto_squash_completed")?;
    let manifest = LayerStack::open(fixture.root.clone())?.read_active_manifest()?;
    let expected_hash = eos_cas::manifest_root_hash(&manifest);
    let invocation_id = format!("autosquash-completed-{}", unique_suffix());
    let request = Request {
        op: "api.v1.write_file".to_owned(),
        invocation_id: invocation_id.clone(),
        args: json!({"layer_stack_root": &fixture.root}),
    };
    let response = json!({
        "timings": {
            "layer_stack.auto_squash.depth_before": 101.0,
            "layer_stack.auto_squash.depth_after": 3.0,
            "layer_stack.auto_squash.total_s": 0.25,
            "layer_stack.auto_squash.manifest_version": i64_to_f64_saturating(manifest.version),
        }
    });
    let after_seq = audit_after_seq()?;

    emit_auto_squash_audit(&request, &response);

    let events = layer_stack_events_after(after_seq, &invocation_id)?;
    assert_eq!(
        event_types(&events),
        vec![
            "layer_stack.squash_triggered",
            "layer_stack.squash_completed"
        ]
    );
    assert_eq!(
        events[0]["payload"]["layer_stack"]["squash_trigger_reason"],
        "post_publish_depth"
    );
    assert_eq!(
        events[0]["payload"]["layer_stack"]["squash_input_layers"],
        101
    );
    assert_eq!(
        events[1]["payload"]["layer_stack"]["squash_result_layers"],
        3
    );
    assert_eq!(
        events[1]["payload"]["layer_stack"]["manifest_root_hash"],
        expected_hash
    );
    Ok(())
}

#[test]
fn auto_squash_audit_emits_triggered_and_failed_for_race() -> TestResult {
    let invocation_id = format!("autosquash-raced-{}", unique_suffix());
    let request = Request {
        op: "api.v1.write_file".to_owned(),
        invocation_id: invocation_id.clone(),
        args: json!({}),
    };
    let response = json!({
        "timings": {
            "layer_stack.auto_squash.depth_before": 102.0,
            "layer_stack.auto_squash.total_s": 0.10,
            "layer_stack.auto_squash.raced": 1.0,
        }
    });
    let after_seq = audit_after_seq()?;

    emit_auto_squash_audit(&request, &response);

    let events = layer_stack_events_after(after_seq, &invocation_id)?;
    assert_eq!(
        event_types(&events),
        vec!["layer_stack.squash_triggered", "layer_stack.squash_failed"]
    );
    assert_eq!(
        events[1]["payload"]["layer_stack"]["squash_failure_kind"],
        "raced_or_plan_aborted"
    );
    assert_eq!(
        events[1]["payload"]["layer_stack"]["squash_trigger_reason"],
        "post_publish_depth"
    );
    Ok(())
}

fn unique_suffix() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    format!(
        "{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    )
}

fn timing_f64_value(timings: &serde_json::Map<String, Value>, key: &str) -> f64 {
    timings.get(key).and_then(Value::as_f64).unwrap_or(0.0)
}

fn audit_after_seq() -> TestResult<i64> {
    let snapshot = op_audit_snapshot(&json!({}), DispatchContext::empty())?;
    Ok(snapshot["snapshot"]["daemon"]["next_seq"]
        .as_i64()
        .unwrap_or(0)
        - 1)
}

fn layer_stack_events_after(after_seq: i64, invocation_id: &str) -> TestResult<Vec<Value>> {
    let pulled = op_audit_pull(
        &json!({"after_seq": after_seq, "limit": 128}),
        DispatchContext::empty(),
    )?;
    Ok(pulled["events"]
        .as_array()
        .ok_or("events array")?
        .iter()
        .filter(|event| {
            event["payload"]["layer_stack"]["operation_id"].as_str() == Some(invocation_id)
        })
        .cloned()
        .collect())
}

fn event_types(events: &[Value]) -> Vec<&str> {
    events
        .iter()
        .filter_map(|event| event["type"].as_str())
        .collect()
}

struct Fixture {
    base: PathBuf,
    root: PathBuf,
}

impl Fixture {
    fn new(label: &str) -> TestResult<Self> {
        Self::new_with_gitignores(label, &[])
    }

    /// Seed one base layer with a `.gitignore` per `(dir, contents)` entry
    /// (`""` = workspace root) so nested / depth-sensitive routing is testable.
    fn new_with_gitignores(label: &str, gitignores: &[(&str, &str)]) -> TestResult<Self> {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let base = std::env::temp_dir().join(format!(
            "eosd-occ-{label}-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("layer-stack");
        let layer = root.join("layers").join("B000001-base");
        std::fs::create_dir_all(&layer)?;
        std::fs::create_dir_all(root.join("staging"))?;
        std::fs::write(layer.join("README.md"), "# README\n")?;
        for (dir, contents) in gitignores {
            let target = if dir.is_empty() {
                layer.join(".gitignore")
            } else {
                layer.join(dir).join(".gitignore")
            };
            if let Some(parent) = target.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(target, contents)?;
        }
        std::fs::write(
            root.join("manifest.json"),
            serde_json::to_string_pretty(&json!({
                "schema_version": 1,
                "version": 1,
                "layers": [{"layer_id": "B000001-base", "path": "layers/B000001-base"}],
            }))?,
        )?;
        Ok(Self { base, root })
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.base);
    }
}
