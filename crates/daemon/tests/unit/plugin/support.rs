use crate::error::DaemonError;
use crate::wire::Request;
use crate::DispatchContext;
use crate::RuntimeServices;
use config::configs::daemon::PluginRuntimeConfig;
use config::configs::isolated_workspace::IsolatedWorkspaceConfig;
use namespace::protocol::{RunRequest, RunResult, RunnerVerb};
use operation::OpRequest;
use protocol::catalog::BuiltinOp;
use serde_json::{json, Value};
use std::error::Error;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use workspace::{LaunchError, NsRunnerLauncher};

pub(super) type TestError = Box<dyn Error + Send + Sync + 'static>;
pub(super) type TestResult = Result<(), TestError>;

/// One isolated daemon under test: its own runtime services instance (no
/// process-global state survives between tests). These adapter tests never
/// start service processes; service runtime behavior lives in the
/// operation-runtime tests.
pub(super) struct TestDaemon {
    services: RuntimeServices,
}

impl TestDaemon {
    pub(super) fn new() -> Self {
        Self::with_configs(
            PluginRuntimeConfig::default(),
            IsolatedWorkspaceConfig::default(),
        )
    }

    pub(super) fn with_isolated_workspace(scratch_root: &Path, workspace_root: &Path) -> Self {
        Self::with_configs(
            PluginRuntimeConfig::default(),
            IsolatedWorkspaceConfig {
                enabled: true,
                scratch_root: scratch_root.to_path_buf(),
                workspace_root: workspace_root.to_path_buf(),
                ..IsolatedWorkspaceConfig::default()
            },
        )
    }

    fn with_configs(plugin: PluginRuntimeConfig, isolated: IsolatedWorkspaceConfig) -> Self {
        Self {
            services: RuntimeServices::new(
                plugin,
                isolated,
                command::CommandConfig::default(),
                Arc::new(NoLaunch),
            ),
        }
    }

    pub(super) fn context(&self) -> DispatchContext<'_> {
        DispatchContext::with_services(&self.services)
    }

    pub(super) fn dispatch(&self, request: &Request) -> Value {
        crate::dispatcher::dispatch_with_context(request, self.context())
    }

    /// `sandbox.plugin.ensure` through the adapter (arg parsing + response shaping
    /// + caller gate), without the dispatcher error-response decoration.
    pub(super) fn op_ensure(&self, args: &Value) -> Result<Value, DaemonError> {
        let OpRequest::PluginEnsure(input) =
            OpRequest::parse(BuiltinOp::PluginEnsure, args, "plugin-test-invocation")
                .expect("valid plugin ensure args")
        else {
            unreachable!("plugin ensure op must parse into plugin ensure input");
        };
        crate::op_adapter::plugin::op_ensure(*input, self.context())
    }

    /// `sandbox.plugin.status` through the adapter.
    pub(super) fn op_status(&self, args: &Value) -> Result<Value, DaemonError> {
        let OpRequest::PluginStatus(input) =
            OpRequest::parse(BuiltinOp::PluginStatus, args, "plugin-test-invocation")
                .expect("valid plugin status args")
        else {
            unreachable!("plugin status op must parse into plugin status input");
        };
        crate::op_adapter::plugin::op_status(input, self.context())
    }
}

struct NoLaunch;

impl NsRunnerLauncher for NoLaunch {
    fn run(&self, request: &RunRequest) -> Result<RunResult, LaunchError> {
        if request.tool_call.verb != RunnerVerb::PluginSetup {
            return Err(LaunchError::Failed(
                "test launcher does not start ns-runner".to_owned(),
            ));
        }
        let command: Vec<String> =
            serde_json::from_value(request.tool_call.args["command"].clone())
                .map_err(|err| LaunchError::InvalidRequest(err.to_string()))?;
        let env: std::collections::BTreeMap<String, String> =
            serde_json::from_value(request.tool_call.args["env"].clone())
                .map_err(|err| LaunchError::InvalidRequest(err.to_string()))?;
        let cwd: PathBuf = serde_json::from_value(request.tool_call.args["cwd"].clone())
            .map_err(|err| LaunchError::InvalidRequest(err.to_string()))?;
        let (program, args) = command
            .split_first()
            .ok_or_else(|| LaunchError::InvalidRequest("empty setup command".to_owned()))?;
        let output = Command::new(program)
            .args(args)
            .current_dir(cwd)
            .env_clear()
            .envs(env)
            .stdin(Stdio::null())
            .output()?;
        Ok(RunResult {
            exit_code: output.status.code().unwrap_or(1),
            payload: json!({
                "success": output.status.success(),
                "status": if output.status.success() { "ok" } else { "error" },
                "exit_code": output.status.code().unwrap_or(1),
                "stdout_tail": String::from_utf8_lossy(&output.stdout),
                "stderr_tail": String::from_utf8_lossy(&output.stderr),
            }),
        })
    }

    fn spawn_detached(
        &self,
        _request: &RunRequest,
        _stderr_path: &std::path::Path,
    ) -> Result<Child, LaunchError> {
        Err(LaunchError::Failed(
            "test launcher does not start ns-runner".to_owned(),
        ))
    }

    fn remount_in(
        &self,
        _target_pid: u32,
        _request: &RunRequest,
        _timeout: Duration,
    ) -> Result<(), LaunchError> {
        Err(LaunchError::Failed(
            "test launcher does not start ns-runner".to_owned(),
        ))
    }
}

pub(super) fn value_array<'a>(
    value: &'a Value,
    context: &'static str,
) -> Result<&'a Vec<Value>, TestError> {
    value
        .as_array()
        .ok_or_else(|| std::io::Error::other(context).into())
}

pub(super) fn value_str<'a>(value: &'a Value, context: &'static str) -> Result<&'a str, TestError> {
    value
        .as_str()
        .ok_or_else(|| std::io::Error::other(context).into())
}

pub(super) fn some_value<T>(value: Option<T>, context: &'static str) -> Result<T, TestError> {
    value.ok_or_else(|| std::io::Error::other(context).into())
}

pub(super) fn generic_service_manifest(digest: &str, op_name: &str) -> Value {
    json!({
        "plugin_id": "generic",
        "plugin_version": "0.1.0",
        "plugin_digest": digest,
        "services": [{
            "service_id": "worker",
            "service_profile_digest": format!("profile-{digest}"),
            "service_mode": "workspace_snapshot_refresh",
            "refresh_strategy": "remount_workspace_and_notify",
            "command": ["generic-service", "--stdio"],
            "ppc_protocol_version": 1
        }],
        "operations": [{
            "op_name": op_name,
            "intent": "read_only",
            "service_id": "worker"
        }]
    })
}

pub(super) fn generic_service_manifest_with_command(
    digest: &str,
    op_name: &str,
    command: Vec<&str>,
) -> Value {
    let mut manifest = generic_service_manifest(digest, op_name);
    manifest["services"][0]["command"] =
        Value::Array(command.into_iter().map(|item| json!(item)).collect());
    manifest
}

pub(super) fn generic_self_managed_manifest(digest: &str, op_name: &str) -> Value {
    let mut manifest = generic_service_manifest(digest, op_name);
    manifest["operations"][0]["intent"] = json!("write_allowed");
    manifest["operations"][0]["auto_workspace_overlay"] = json!(false);
    manifest
}

pub(super) fn oneshot_overlay_manifest(digest: &str, op_name: &str) -> Value {
    json!({
        "plugin_id": "generic",
        "plugin_version": "0.1.0",
        "plugin_digest": digest,
        "services": [{
            "service_id": "worker",
            "service_profile_digest": format!("oneshot-profile-{digest}"),
            "service_mode": "oneshot_overlay",
            "refresh_strategy": "restart_service",
            "command": ["python3", "/eos/plugin/oneshot.py"],
            "ppc_protocol_version": 1
        }],
        "operations": [{
            "op_name": op_name,
            "intent": "write_allowed",
            "service_id": "worker",
            "timeout_ms": 5000
        }]
    })
}

pub(super) fn test_layer_stack_root(name: &str) -> Result<PathBuf, TestError> {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let base = std::env::temp_dir().join(format!(
        "plugin-{name}-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = std::fs::remove_dir_all(&base);
    let root = base.join("layer-stack");
    std::fs::create_dir_all(&root)?;
    Ok(root)
}

pub(super) fn test_bound_workspace(name: &str) -> Result<(PathBuf, PathBuf), TestError> {
    let layer_stack_root = test_layer_stack_root(name)?;
    let base = some_value(layer_stack_root.parent(), "layer root must have a parent")?;
    let workspace_root = base.join("workspace");
    std::fs::create_dir_all(&workspace_root)?;
    std::fs::write(workspace_root.join("seed.txt"), "seed\n")?;
    layerstack::build_workspace_base(&layer_stack_root, &workspace_root, true)?;
    Ok((layer_stack_root, workspace_root))
}

pub(super) fn remove_test_tree(layer_stack_root: &Path) -> TestResult {
    let base = some_value(
        layer_stack_root.parent(),
        "test layer root must have a parent",
    )?;
    let _ = std::fs::remove_dir_all(base);
    Ok(())
}
