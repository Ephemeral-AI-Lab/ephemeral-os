use std::sync::Arc;
use std::thread;

use sandbox_runtime_namespace_process::runner::protocol::NamespaceRunnerRequest;
use serde_json::Value;

use crate::error::NamespaceExecutionError;
use crate::execution::{ExecutionHandle, InteractiveExecution};
use crate::id::NamespaceExecutionId;
use crate::launcher::{ForkRunnerLauncher, NsRunnerLauncher, RunnerChild};
use crate::observer::ExecutionObserver;
use crate::promise::CompletionPromise;
use crate::registry::{CompletedExecution, ExecutionRegistry};
use crate::shell::{RunnerOutcome, ShellOperation};
use crate::status::NamespaceExecutionTerminalStatus;
use crate::target::NamespaceTarget;

/// Strategy + Template-Method core: holds the registry, observer, and boxed
/// launcher (the Bridge seam, §2.1). Both entry points share one dispatch spine;
/// the engine knows nothing of shell-vs-mount beyond which launcher method and
/// finalizer it is handed.
pub struct NamespaceExecutionEngine {
    registry: Arc<ExecutionRegistry>,
    observer: Arc<dyn ExecutionObserver>,
    launcher: Box<dyn NsRunnerLauncher>,
}

impl NamespaceExecutionEngine {
    #[must_use]
    pub fn new(observer: Arc<dyn ExecutionObserver>, max_active: usize) -> Self {
        Self {
            registry: Arc::new(ExecutionRegistry::new(max_active)),
            observer,
            launcher: Box::new(ForkRunnerLauncher),
        }
    }

    #[cfg(feature = "test-support")]
    pub fn with_launcher(
        launcher: Box<dyn NsRunnerLauncher>,
        observer: Arc<dyn ExecutionObserver>,
        max_active: usize,
    ) -> Self {
        Self {
            registry: Arc::new(ExecutionRegistry::new(max_active)),
            observer,
            launcher,
        }
    }

    /// PTY-backed shell execution. The runner runs in `Run` mode (no mode flag).
    pub fn run_shell_interactive<S: ShellOperation>(
        &self,
        op: S,
        target: NamespaceTarget,
        id: NamespaceExecutionId,
    ) -> Result<InteractiveExecution<S::Output>, NamespaceExecutionError> {
        self.registry.try_reserve(&id)?;
        let request = build_request(&target, &id, shell_args(op.command()), op.timeout_seconds());
        let op = Box::new(op);
        let (child, pty) = match self.launcher.spawn_pty(request) {
            Ok(spawned) => spawned,
            Err(error) => {
                self.registry.abort(&id);
                return Err(error);
            }
        };
        self.registry.attach(&id, pty.pgid());
        self.observer.on_running(&id);
        let promise = Arc::new(CompletionPromise::new());
        self.spawn_watcher(id.clone(), child, Arc::clone(&promise), move |outcome| {
            op.finalize(outcome)
        });
        Ok(InteractiveExecution::new(
            ExecutionHandle::new(id, promise),
            pty,
        ))
    }

    /// Pipe-backed mount/remount execution. `mode_flag` selects the runner mode
    /// (`--mount-overlay` / `--remount-overlay`); `parse` projects the outcome.
    pub fn run_mount<O: Send + 'static>(
        &self,
        mode_flag: &'static str,
        target: NamespaceTarget,
        id: NamespaceExecutionId,
        parse: impl FnOnce(RunnerOutcome) -> Result<O, NamespaceExecutionError> + Send + 'static,
    ) -> Result<ExecutionHandle<O>, NamespaceExecutionError> {
        self.registry.try_reserve(&id)?;
        let request = build_request(&target, &id, Value::Object(serde_json::Map::new()), None);
        let child = match self.launcher.spawn_piped(mode_flag, request) {
            Ok(child) => child,
            Err(error) => {
                self.registry.abort(&id);
                return Err(error);
            }
        };
        self.registry.attach(&id, None);
        self.observer.on_running(&id);
        let promise = Arc::new(CompletionPromise::new());
        self.spawn_watcher(id.clone(), child, Arc::clone(&promise), parse);
        Ok(ExecutionHandle::new(id, promise))
    }

    /// The watcher thread: one blocking `wait_completion`, then finalize inline,
    /// `complete` BEFORE `resolve` (so promise-resolved ⟹ the completed entry
    /// exists), then `on_terminal`. No poll loops.
    fn spawn_watcher<O: Send + 'static>(
        &self,
        id: NamespaceExecutionId,
        mut child: Box<dyn RunnerChild>,
        promise: Arc<CompletionPromise<O>>,
        finalize: impl FnOnce(RunnerOutcome) -> Result<O, NamespaceExecutionError> + Send + 'static,
    ) {
        let registry = Arc::clone(&self.registry);
        let observer = Arc::clone(&self.observer);
        thread::spawn(move || {
            let (result, status, exit_code) = match child.wait_completion() {
                Ok(run_result) => {
                    let outcome = RunnerOutcome::new(run_result);
                    let status = outcome.status();
                    let exit_code = Some(outcome.exit_code());
                    match finalize(outcome) {
                        Ok(output) => (Ok(output), status, exit_code),
                        Err(error) => (
                            Err(error),
                            NamespaceExecutionTerminalStatus::Error,
                            exit_code,
                        ),
                    }
                }
                Err(error) => (Err(error), NamespaceExecutionTerminalStatus::Error, None),
            };
            registry.complete(&id, CompletedExecution { status, exit_code });
            promise.resolve(result);
            observer.on_terminal(&id, status, exit_code);
        });
    }
}

fn shell_args(command: &str) -> Value {
    serde_json::json!({ "command": command, "cwd": "." })
}

fn build_request(
    target: &NamespaceTarget,
    id: &NamespaceExecutionId,
    args: Value,
    timeout_seconds: Option<f64>,
) -> NamespaceRunnerRequest {
    NamespaceRunnerRequest {
        request_id: id.0.clone(),
        args,
        workspace_root: target.workspace_root.clone(),
        layer_paths: target.layer_paths.clone(),
        upperdir: target.upperdir.clone(),
        workdir: target.workdir.clone(),
        ns_fds: Some(target.ns_fds),
        timeout_seconds,
    }
}
