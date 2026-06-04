use std::time::Instant;

use eos_protocol::Intent;

use crate::cleanup::cleanup_ephemeral_workspace;
use crate::dirs::EphemeralDirAllocator;
use crate::error::EphemeralWorkspaceError;
use crate::ports::{EphemeralSnapshotPort, FreshNamespaceRunnerPort};
use crate::runner::run_fresh_namespace;
use crate::types::{AgentId, EphemeralToolSpec, EphemeralWorkspace, InvocationId, WorkspaceRoot};

/// Request for a read-only fresh overlay tool execution.
#[derive(Debug, Clone, PartialEq)]
pub struct ReadToolRequest {
    pub layer_stack_root: WorkspaceRoot,
    pub workspace_root: std::path::PathBuf,
    pub agent_id: AgentId,
    pub invocation_id: InvocationId,
    pub verb: String,
    pub args: serde_json::Value,
    pub timeout_seconds: Option<f64>,
}

/// Outcome from a read-only fresh overlay tool execution.
#[derive(Debug, Clone, PartialEq)]
pub struct ReadToolOutcome {
    pub runner: eos_runner::RunResult,
    pub lease_acquire_s: f64,
    pub total_s: f64,
}

/// Run a read-only tool in a fresh overlay, always cleaning up lease and scratch.
///
/// # Errors
///
/// Returns [`EphemeralWorkspaceError`] when snapshot acquisition, directory
/// allocation, or runner execution fails. Cleanup is still attempted after
/// allocation succeeds.
pub fn run_read_tool<S, R>(
    snapshots: &S,
    runner: &R,
    dirs: &EphemeralDirAllocator,
    request: ReadToolRequest,
) -> Result<ReadToolOutcome, EphemeralWorkspaceError>
where
    S: EphemeralSnapshotPort,
    R: FreshNamespaceRunnerPort,
{
    let total_start = Instant::now();
    let lease_start = Instant::now();
    let snapshot = snapshots.acquire_snapshot(
        &request.layer_stack_root,
        &format!("overlay:{}:{}", request.agent_id.0, request.invocation_id.0),
    )?;
    let lease_acquire_s = lease_start.elapsed().as_secs_f64();

    let run_dirs = match dirs.allocate("sandbox-overlay", &request.invocation_id) {
        Ok(run_dirs) => run_dirs,
        Err(error) => {
            let _ = snapshots.release_lease(&request.layer_stack_root, &snapshot.lease_id);
            return Err(error);
        }
    };

    let workspace = EphemeralWorkspace {
        layer_stack_root: request.layer_stack_root.clone(),
        workspace_root: request.workspace_root,
        agent_id: request.agent_id,
        invocation_id: request.invocation_id,
        snapshot: snapshot.clone(),
        dirs: run_dirs,
    };
    let spec = EphemeralToolSpec {
        verb: request.verb,
        intent: Intent::ReadOnly,
        args: request.args,
        background: false,
        timeout_seconds: request.timeout_seconds,
    };

    let runner_result = run_fresh_namespace(runner, &workspace, &spec);
    let cleanup = cleanup_ephemeral_workspace(
        snapshots,
        &workspace.layer_stack_root,
        &snapshot,
        &workspace.dirs.run_dir,
    );

    let runner = runner_result?;
    if let Some(error) = cleanup.errors.into_iter().next() {
        return Err(EphemeralWorkspaceError::CleanupFailed {
            path: workspace.dirs.run_dir,
            reason: error,
        });
    }

    Ok(ReadToolOutcome {
        runner,
        lease_acquire_s,
        total_s: total_start.elapsed().as_secs_f64(),
    })
}
