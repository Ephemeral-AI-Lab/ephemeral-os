use std::future;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};
use std::thread;
use std::time::Duration;

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
    let upperdir_stats = eos_workspace::TreeResourceStats::collect(&upperdir);
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
fn builtin_table_routes_commit_to_workspace() {
    let response = dispatch(&Request {
        op: "sandbox.checkpoint.commit_to_workspace".to_owned(),
        invocation_id: "commit-to-workspace-route-test".to_owned(),
        args: json!({}),
    });

    assert_ne!(response["error"]["kind"], json!("unknown_op"));
    assert_eq!(response["error"]["kind"], json!("invalid_request"));
    assert!(response["error"]["message"]
        .as_str()
        .unwrap_or_default()
        .contains("layer_stack_root is required"));
}

#[test]
fn builtin_table_routes_commit_to_git() {
    let response = dispatch(&Request {
        op: "sandbox.checkpoint.commit_to_git".to_owned(),
        invocation_id: "commit-to-git-route-test".to_owned(),
        args: json!({}),
    });

    assert_ne!(response["error"]["kind"], json!("unknown_op"));
    assert_eq!(response["error"]["kind"], json!("invalid_request"));
    assert!(response["error"]["message"]
        .as_str()
        .unwrap_or_default()
        .contains("layer_stack_root is required"));
}

#[test]
fn builtin_parse_gate_preserves_error_response_channel() {
    let response = dispatch(&Request {
        op: "sandbox.file.edit".to_owned(),
        invocation_id: "edit-parse-gate".to_owned(),
        args: json!({}),
    });

    assert_eq!(response["error"]["kind"], json!("invalid_request"));
    assert_eq!(
        response["error"]["message"],
        json!("invalid request: edits must be a list")
    );
    assert_eq!(response["warnings"], json!([]));
}

#[test]
fn builtin_parse_gate_preserves_refused_channel() {
    let response = dispatch(&Request {
        op: "sandbox.isolation.enter".to_owned(),
        invocation_id: "isolation-parse-gate".to_owned(),
        args: json!({}),
    });

    assert_eq!(response["success"], json!(false));
    assert_eq!(response["error"]["kind"], json!("invalid_argument"));
    assert_eq!(response["error"]["message"], json!("caller_id is required"));
    assert_eq!(response["error"]["details"], json!({"key": "caller_id"}));
    assert!(response.get("warnings").is_none());
}

#[test]
fn command_poll_parse_gate_preserves_id_first_error() {
    let response = dispatch(&Request {
        op: "sandbox.command.poll".to_owned(),
        invocation_id: "command-poll-parse-gate".to_owned(),
        args: json!({"last_n_lines": u64::MAX}),
    });

    assert_eq!(response["error"]["kind"], json!("invalid_request"));
    assert_eq!(
        response["error"]["message"],
        json!("invalid request: command_id is required")
    );
}

#[test]
fn dispatch_attaches_real_runtime_timings() {
    let response = dispatch_with_context(
        &Request {
            op: "sandbox.call.heartbeat".to_owned(),
            invocation_id: "timings-test".to_owned(),
            args: json!({"invocation_ids": []}),
        },
        DispatchContext::with_read_request_s(0.125),
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
            >= 0.0
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

    let response = dispatch_with_context(
        &Request {
            op: "sandbox.call.cancel".to_owned(),
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
fn internal_error_response_adds_error_id() {
    let response = error_response(
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

fn timing_f64_value(timings: &serde_json::Map<String, Value>, key: &str) -> f64 {
    timings.get(key).and_then(Value::as_f64).unwrap_or(0.0)
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
