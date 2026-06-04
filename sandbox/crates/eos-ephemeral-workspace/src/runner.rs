use eos_runner::{RunMode, RunRequest, RunnerVerb, ToolCall};

use crate::error::EphemeralWorkspaceError;
use crate::ports::FreshNamespaceRunnerPort;
use crate::types::{EphemeralToolSpec, EphemeralWorkspace};

/// Builds runner requests for fresh namespace executions.
pub struct FreshRunRequestBuilder;

impl FreshRunRequestBuilder {
    #[must_use]
    pub fn build(workspace: &EphemeralWorkspace, spec: &EphemeralToolSpec) -> RunRequest {
        RunRequest {
            mode: RunMode::FreshNs,
            tool_call: ToolCall {
                invocation_id: workspace.invocation_id.0.clone(),
                agent_id: workspace.agent_id.0.clone(),
                verb: RunnerVerb::from(spec.verb.clone()),
                intent: spec.intent,
                args: spec.args.clone(),
                background: spec.background,
            },
            workspace_root: eos_runner::WorkspaceRoot(workspace.workspace_root.clone()),
            layer_paths: workspace.snapshot.layer_paths.clone(),
            upperdir: Some(workspace.dirs.upperdir.clone()),
            workdir: Some(workspace.dirs.workdir.clone()),
            ns_fds: None,
            cgroup_path: None,
            timeout_seconds: spec.timeout_seconds,
        }
    }
}

/// Run a tool call in a fresh namespace via the injected daemon runner port.
///
/// # Errors
///
/// Returns [`EphemeralWorkspaceError`] when the runner port fails.
pub fn run_fresh_namespace<R>(
    runner: &R,
    workspace: &EphemeralWorkspace,
    spec: &EphemeralToolSpec,
) -> Result<eos_runner::RunResult, EphemeralWorkspaceError>
where
    R: FreshNamespaceRunnerPort,
{
    let request = FreshRunRequestBuilder::build(workspace, spec);
    runner.run(&request)
}
