//! Plugin op adapter tests: wire arg parsing, response shaping, registered-op
//! routing through the dispatcher, and the isolated-caller gate. Service
//! process behavior (start/refresh/restart/health) lives in
//! the operation-runtime tests.

mod support;

use support::*;

use crate::wire::Request;
use serde_json::json;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

#[test]
fn ensure_records_manifest_services_and_status_lists_them() -> TestResult {
    let daemon = TestDaemon::new();
    let response = daemon.op_ensure(&json!({
        "manifest": generic_service_manifest("digest-a", "hover"),
        "layer_stack_root": "/eos/plugin/layer-stack",
        "workspace_root": "/eos/plugin/workspace"
    }))?;
    assert_eq!(response["success"], true);
    assert_eq!(response["registered_ops"], json!(["plugin.generic.hover"]));
    assert_eq!(
        response["operation_routes"][0]["dispatch_mode"],
        "read_only_service"
    );
    assert_eq!(response["services"][0]["state"], "stopped");
    assert_eq!(response["service_processes"][0]["service_id"], "worker");
    assert!(value_str(
        &response["service_processes"][0]["socket_path"],
        "socket path must be a string"
    )?
    .starts_with("/eos/plugin/ppc/"));

    let status = daemon.op_status(&json!({}))?;
    assert_eq!(status["loaded_plugins"][0]["name"], "generic");
    Ok(())
}

#[test]
fn ensure_exposes_package_roots_to_service_process_specs() -> TestResult {
    let daemon = TestDaemon::new();
    let response = daemon.op_ensure(&json!({
        "manifest": generic_service_manifest("digest-a", "hover"),
        "layer_stack_root": "/eos/plugin/layer-stack",
        "workspace_root": "/eos/plugin/workspace"
    }))?;
    let process = &response["service_processes"][0];
    assert_eq!(
        process["package_root"],
        "/eos/runtime/plugins/catalog/generic/digest-a"
    );
    assert_eq!(
        process["dependency_root"],
        "/eos/runtime/packages/generic/digest-a"
    );
    assert_eq!(
        process["working_dir"],
        "/eos/runtime/plugins/catalog/generic/digest-a"
    );
    assert_eq!(
        process["env"]["EOS_PLUGIN_PACKAGE_ROOT"],
        "/eos/runtime/plugins/catalog/generic/digest-a"
    );
    assert_eq!(
        process["env"]["EOS_PLUGIN_DEPENDENCY_ROOT"],
        "/eos/runtime/packages/generic/digest-a"
    );
    Ok(())
}

#[test]
fn ensure_resolves_service_relative_command_under_package_working_dir() -> TestResult {
    let daemon = TestDaemon::new();
    let mut manifest =
        generic_service_manifest_with_command("digest-a", "hover", vec!["./server.py"]);
    manifest["services"][0]["working_dir"] = json!("runtime");
    let response = daemon.op_ensure(&json!({
        "manifest": manifest,
        "layer_stack_root": "/eos/plugin/layer-stack",
        "workspace_root": "/eos/plugin/workspace"
    }))?;
    let expected = "/eos/runtime/plugins/catalog/generic/digest-a/runtime/server.py";
    assert_eq!(response["service_processes"][0]["command"][0], expected);
    assert_eq!(
        response["operation_routes"][0]["service_command"][0],
        expected
    );
    assert_eq!(
        response["service_processes"][0]["working_dir"],
        "/eos/runtime/plugins/catalog/generic/digest-a/runtime"
    );
    Ok(())
}

#[test]
fn ensure_is_idempotent_for_same_digest() -> TestResult {
    let daemon = TestDaemon::new();
    let first = daemon.op_ensure(&json!({"plugin": "demo", "digest": "a"}))?;
    let second = daemon.op_ensure(&json!({"plugin": "demo", "digest": "a"}))?;
    assert_eq!(first["already_loaded"], false);
    assert_eq!(second["already_loaded"], true);
    Ok(())
}

#[test]
fn package_warm_missing_returns_needs_upload() -> TestResult {
    let daemon = TestDaemon::new();
    let roots = PackageTestRoots::new("warm-missing")?;
    let response = daemon.op_ensure(&roots.args(
        package_manifest("digest-a", "setup-a", vec!["./setup.sh"]),
        None,
    ))?;
    assert_eq!(response["success"], true);
    assert_eq!(response["needs_upload"], true);
    assert_eq!(response["ready"], false);
    assert_eq!(response["plugin"], "generic");
    roots.cleanup();
    Ok(())
}

#[test]
fn package_cold_publish_setup_and_warm_reensure_are_idempotent() -> TestResult {
    let daemon = TestDaemon::new();
    let roots = PackageTestRoots::new("cold-publish")?;
    let staged = roots.stage_package(
        "digest-a",
        r#"#!/bin/sh
set -eu
count_file="$EOS_PLUGIN_DEPENDENCY_ROOT/cache/setup-count"
count=0
if [ -f "$count_file" ]; then count="$(cat "$count_file")"; fi
count=$((count + 1))
printf '%s' "$count" > "$count_file"
printf tmp > "$TMPDIR/setup.tmp"
"#,
    )?;
    let manifest = package_manifest("digest-a", "setup-a", vec!["./setup.sh"]);

    let cold = daemon.op_ensure(&roots.args(manifest.clone(), Some(&staged)))?;
    assert_eq!(cold["success"], true);
    assert_eq!(cold["package"]["package_published"], true);
    assert_eq!(cold["package"]["setup_ran"], true);
    assert!(roots
        .package_root("digest-a")
        .join(".package-sha256")
        .is_file());
    assert!(roots
        .package_root("digest-a")
        .join(".setup-sha256")
        .is_file());
    assert_eq!(
        std::fs::read_to_string(roots.dependency_root("digest-a").join("cache/setup-count"))?,
        "1"
    );
    assert!(roots.setup_root("digest-a").join("tmp/setup.tmp").is_file());

    let warm = daemon.op_ensure(&roots.args(manifest, None))?;
    assert_eq!(warm["success"], true);
    assert_eq!(warm["package"]["needs_upload"], false);
    assert_eq!(warm["package"]["setup_ran"], false);
    assert_eq!(
        std::fs::read_to_string(roots.dependency_root("digest-a").join("cache/setup-count"))?,
        "1"
    );

    roots.cleanup();
    Ok(())
}

#[test]
fn package_changed_digest_runs_setup_for_new_dependency_root() -> TestResult {
    let daemon = TestDaemon::new();
    let roots = PackageTestRoots::new("changed-digest")?;
    let setup_script = r#"#!/bin/sh
set -eu
printf setup > "$EOS_PLUGIN_DEPENDENCY_ROOT/cache/setup-ran"
"#;

    let staged_a = roots.stage_package("digest-a", setup_script)?;
    let cold_a = daemon.op_ensure(&roots.args(
        package_manifest("digest-a", "setup-a", vec!["./setup.sh"]),
        Some(&staged_a),
    ))?;
    assert_eq!(cold_a["package"]["setup_ran"], true);

    let staged_b = roots.stage_package("digest-b", setup_script)?;
    let cold_b = daemon.op_ensure(&roots.args(
        package_manifest("digest-b", "setup-b", vec!["./setup.sh"]),
        Some(&staged_b),
    ))?;
    assert_eq!(cold_b["package"]["setup_ran"], true);
    assert!(roots
        .dependency_root("digest-a")
        .join("cache/setup-ran")
        .is_file());
    assert!(roots
        .dependency_root("digest-b")
        .join("cache/setup-ran")
        .is_file());

    roots.cleanup();
    Ok(())
}

#[test]
fn package_rejects_staging_outside_digest_upload_root() -> TestResult {
    let daemon = TestDaemon::new();
    let roots = PackageTestRoots::new("bad-stage")?;
    let outside = roots.root.join("outside/package");
    std::fs::create_dir_all(&outside)?;
    let err = daemon
        .op_ensure(&roots.args(
            package_manifest("digest-a", "setup-a", vec!["./setup.sh"]),
            Some(&outside),
        ))
        .expect_err("staging outside digest upload root must be rejected");
    assert!(err
        .to_string()
        .contains("staged_package_root must be under"));
    roots.cleanup();
    Ok(())
}

#[test]
fn package_setup_failure_is_visible_in_status_and_prevents_service_start() -> TestResult {
    let daemon = TestDaemon::new();
    let roots = PackageTestRoots::new("setup-failure")?;
    let staged = roots.stage_package("digest-a", "#!/bin/sh\nexit 7\n")?;
    let err = daemon
        .op_ensure(&roots.args(
            package_manifest("digest-a", "setup-a", vec!["./setup.sh"]),
            Some(&staged),
        ))
        .expect_err("setup failure must reject package ensure");
    assert!(err.to_string().contains("plugin setup failed"));

    let status = daemon.op_status(&json!({}))?;
    assert_eq!(status["setup_failures"][0]["plugin"], "generic");
    assert_eq!(status["running_service_processes"], json!([]));
    roots.cleanup();
    Ok(())
}

#[test]
fn package_setup_rejects_forbidden_rootfs_writes() -> TestResult {
    let daemon = TestDaemon::new();
    let roots = PackageTestRoots::new("forbidden-root")?;
    let staged = roots.stage_package(
        "digest-a",
        r#"#!/bin/sh
set -eu
touch /root/plugin
"#,
    )?;
    let err = daemon
        .op_ensure(&roots.args(
            package_manifest("digest-a", "setup-a", vec!["./setup.sh"]),
            Some(&staged),
        ))
        .expect_err("setup script with forbidden rootfs write must be rejected");
    assert!(err.to_string().contains("forbidden managed root /root"));
    roots.cleanup();
    Ok(())
}

#[test]
fn ensure_reloads_same_digest_when_workspace_root_changes() -> TestResult {
    let daemon = TestDaemon::new();
    let first = daemon.op_ensure(&json!({
        "manifest": generic_service_manifest("digest-a", "hover"),
        "layer_stack_root": "/eos/plugin/layer-stack",
        "workspace_root": "/testbed"
    }))?;
    let second = daemon.op_ensure(&json!({
        "manifest": generic_service_manifest("digest-a", "hover"),
        "layer_stack_root": "/eos/plugin/layer-stack",
        "workspace_root": "/ephemeral-os"
    }))?;

    assert_eq!(first["already_loaded"], false);
    assert_eq!(second["already_loaded"], false);
    assert_eq!(
        first["service_processes"][0]["env"]["EOS_PLUGIN_WORKSPACE_ROOT"],
        "/testbed"
    );
    assert_eq!(
        second["service_processes"][0]["env"]["EOS_PLUGIN_WORKSPACE_ROOT"],
        "/ephemeral-os"
    );
    Ok(())
}

#[test]
fn op_table_registers_plugin_status_and_ensure() -> TestResult {
    let daemon = TestDaemon::new();
    let ensure = daemon.dispatch(&Request {
        op: "sandbox.plugin.ensure".to_owned(),
        invocation_id: "plugin-ensure-test".to_owned(),
        args: json!({"plugin": "demo", "digest": "a"}),
    });
    assert_eq!(ensure["success"], true);

    let status = daemon.dispatch(&Request {
        op: "sandbox.plugin.status".to_owned(),
        invocation_id: "plugin-status-test".to_owned(),
        args: json!({}),
    });
    assert_eq!(status["success"], true);
    let loaded = value_array(&status["loaded_plugins"], "loaded_plugins must be an array")?;
    assert!(loaded.iter().any(|plugin| plugin["name"] == "demo"));
    Ok(())
}

#[test]
fn registered_plugin_op_routes_to_deferred_dispatch_not_unknown_op() -> TestResult {
    let daemon = TestDaemon::new();
    let ensure = daemon.dispatch(&Request {
        op: "sandbox.plugin.ensure".to_owned(),
        invocation_id: "plugin-ensure-test".to_owned(),
        args: json!({
            "manifest": generic_self_managed_manifest("digest-a", "apply"),
            "layer_stack_root": "/eos/plugin/layer-stack",
            "workspace_root": "/eos/plugin/workspace"
        }),
    });
    assert_eq!(ensure["success"], true);
    assert_eq!(
        ensure["operation_routes"][0]["dispatch_mode"],
        "self_managed_callback"
    );

    let routed = daemon.dispatch(&Request {
        op: "plugin.generic.apply".to_owned(),
        invocation_id: "plugin-apply-test".to_owned(),
        args: json!({"caller_id": "caller-plugin"}),
    });
    assert_eq!(routed["success"], false);
    assert_eq!(routed["status"], "deferred");
    assert_eq!(routed["error"]["kind"], "plugin_dispatch_deferred");
    assert_eq!(routed["dispatch_mode"], "self_managed_callback");

    let missing = daemon.dispatch(&Request {
        op: "plugin.generic.missing".to_owned(),
        invocation_id: "plugin-missing-test".to_owned(),
        args: json!({}),
    });
    assert_eq!(missing["error"]["kind"], "unknown_op");
    Ok(())
}

#[test]
fn dynamic_plugin_op_is_blocked_in_isolated_workspace_before_route_lookup() -> TestResult {
    let _env_guard = crate::op_adapter::isolation::lock_isolated_test_state();
    let (layer_stack_root, _workspace_root) = test_bound_workspace("plugin-iws-block")?;
    let scratch = some_value(
        layer_stack_root.parent(),
        "test layer root must have a parent",
    )?
    .join("scratch");
    let daemon = TestDaemon::with_isolated_workspace(&scratch, Path::new("/testbed"));
    let _harness = TestEnvVar::set("EOS_ISOLATED_WORKSPACE_TEST_HARNESS", "true");

    let _ = daemon.dispatch(&Request {
        op: "sandbox.isolation.test_reset".to_owned(),
        invocation_id: "iws-reset-before-plugin-block".to_owned(),
        args: json!({}),
    });
    let entered = daemon.dispatch(&Request {
        op: "sandbox.isolation.enter".to_owned(),
        invocation_id: "iws-enter-before-plugin-block".to_owned(),
        args: json!({
            "caller_id": "caller-plugin",
            "layer_stack_root": layer_stack_root.to_string_lossy(),
        }),
    });
    assert_eq!(entered["success"], true);

    let blocked = daemon.dispatch(&Request {
        op: "plugin.generic.not_loaded_yet".to_owned(),
        invocation_id: "plugin-dynamic-iws-block".to_owned(),
        args: json!({"caller_id": "caller-plugin"}),
    });
    assert_eq!(blocked["error"]["kind"], "forbidden_in_isolated_workspace");

    let exited = daemon.dispatch(&Request {
        op: "sandbox.isolation.exit".to_owned(),
        invocation_id: "iws-exit-after-plugin-block".to_owned(),
        args: json!({"caller_id": "caller-plugin"}),
    });
    assert_eq!(exited["success"], true);
    let _ = daemon.dispatch(&Request {
        op: "sandbox.isolation.test_reset".to_owned(),
        invocation_id: "iws-reset-after-plugin-block".to_owned(),
        args: json!({}),
    });
    remove_test_tree(&layer_stack_root)?;
    Ok(())
}

#[test]
fn ensure_records_oneshot_overlay_route_without_starting_process() -> TestResult {
    let daemon = TestDaemon::new();
    let response = daemon.op_ensure(&json!({
        "manifest": oneshot_overlay_manifest("digest-a", "write"),
        "layer_stack_root": "/eos/plugin/layer-stack",
        "workspace_root": "/eos/plugin/workspace",
        "start_services": true
    }))?;

    assert_eq!(response["success"], true);
    assert_eq!(response["service_processes"], json!([]));
    assert_eq!(response["service_processes_started"], false);
    assert_eq!(
        response["operation_routes"][0]["dispatch_mode"],
        "write_allowed_oneshot_overlay"
    );
    assert_eq!(
        response["operation_routes"][0]["service_mode"],
        "oneshot_overlay"
    );
    assert_eq!(
        response["operation_routes"][0]["service_command"],
        json!(["python3", "/eos/plugin/oneshot.py"])
    );
    assert_eq!(
        response["services"][0]["last_error"],
        "oneshot overlay worker starts per operation"
    );
    Ok(())
}

#[test]
fn digest_reload_replaces_dynamic_plugin_routes() -> TestResult {
    let daemon = TestDaemon::new();
    let first = daemon.dispatch(&Request {
        op: "sandbox.plugin.ensure".to_owned(),
        invocation_id: "plugin-ensure-a".to_owned(),
        args: json!({
            "manifest": generic_service_manifest("digest-a", "hover"),
            "layer_stack_root": "/eos/plugin/layer-stack",
            "workspace_root": "/eos/plugin/workspace"
        }),
    });
    assert_eq!(first["registered_ops"], json!(["plugin.generic.hover"]));

    let second = daemon.dispatch(&Request {
        op: "sandbox.plugin.ensure".to_owned(),
        invocation_id: "plugin-ensure-b".to_owned(),
        args: json!({
            "manifest": generic_service_manifest("digest-b", "diagnostics"),
            "layer_stack_root": "/eos/plugin/layer-stack",
            "workspace_root": "/eos/plugin/workspace"
        }),
    });
    assert_eq!(
        second["registered_ops"],
        json!(["plugin.generic.diagnostics"])
    );

    let old = daemon.dispatch(&Request {
        op: "plugin.generic.hover".to_owned(),
        invocation_id: "plugin-hover-old".to_owned(),
        args: json!({}),
    });
    assert_eq!(old["error"]["kind"], "unknown_op");

    let current = daemon.dispatch(&Request {
        op: "plugin.generic.diagnostics".to_owned(),
        invocation_id: "plugin-diagnostics-current".to_owned(),
        args: json!({}),
    });
    assert_eq!(current["error"]["kind"], "plugin_dispatch_deferred");
    Ok(())
}

struct TestEnvVar {
    key: &'static str,
    previous: Option<String>,
}

impl TestEnvVar {
    fn set(key: &'static str, value: &str) -> Self {
        let previous = std::env::var(key).ok();
        std::env::set_var(key, value);
        Self { key, previous }
    }
}

impl Drop for TestEnvVar {
    fn drop(&mut self) {
        if let Some(previous) = &self.previous {
            std::env::set_var(self.key, previous);
        } else {
            std::env::remove_var(self.key);
        }
    }
}

struct PackageTestRoots {
    root: PathBuf,
}

impl PackageTestRoots {
    fn new(name: &str) -> Result<Self, TestError> {
        let root =
            std::env::temp_dir().join(format!("eos-plugin-package-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root)?;
        Ok(Self { root })
    }

    fn args(&self, manifest: serde_json::Value, staged: Option<&Path>) -> serde_json::Value {
        let mut args = json!({
            "manifest": manifest,
            "package_runtime_root": self.root.join("runtime/plugins/catalog").to_string_lossy(),
            "package_dependency_root": self.root.join("runtime/packages").to_string_lossy(),
            "package_upload_root": self.root.join("scratch/uploads/plugins").to_string_lossy(),
            "package_setup_root": self.root.join("scratch/setup").to_string_lossy(),
        });
        if let Some(staged) = staged {
            args["staged_package_root"] = json!(staged.to_string_lossy());
        }
        args
    }

    fn stage_package(&self, digest: &str, setup_script: &str) -> Result<PathBuf, TestError> {
        let package = self
            .root
            .join("scratch/uploads/plugins/generic")
            .join(digest)
            .join("upload-1/package");
        std::fs::create_dir_all(package.join("runtime"))?;
        std::fs::write(package.join("sandbox-plugin.json"), "{}\n")?;
        std::fs::write(package.join(".package-sha256"), digest)?;
        let setup = package.join("setup.sh");
        std::fs::write(&setup, setup_script)?;
        std::fs::set_permissions(&setup, std::fs::Permissions::from_mode(0o755))?;
        std::fs::write(package.join("runtime/server.sh"), "#!/bin/sh\n")?;
        Ok(package)
    }

    fn package_root(&self, digest: &str) -> PathBuf {
        self.root
            .join("runtime/plugins/catalog/generic")
            .join(digest)
    }

    fn dependency_root(&self, digest: &str) -> PathBuf {
        self.root.join("runtime/packages/generic").join(digest)
    }

    fn setup_root(&self, digest: &str) -> PathBuf {
        self.root.join("scratch/setup/generic").join(digest)
    }

    fn cleanup(&self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

fn package_manifest(digest: &str, setup_digest: &str, command: Vec<&str>) -> serde_json::Value {
    json!({
        "plugin_id": "generic",
        "plugin_version": "0.1.0",
        "plugin_digest": digest,
        "package": {
            "runtime_dir": "runtime",
            "dependency_scope": "package_digest"
        },
        "setup": {
            "command": command,
            "working_dir": ".",
            "setup_marker_digest": setup_digest,
            "timeout_ms": 30_000
        },
        "services": [],
        "operations": []
    })
}
