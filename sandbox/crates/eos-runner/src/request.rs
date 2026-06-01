//! Owned request/result types for the namespace runner.
//!
//! These model the JSON payloads the Python helpers exchange over stdin/stdout
//! and the namespace request/result files — `to_payload()`
//! (`shared/models.py:90-98`), the fresh-ns request file
//! (`overlay/namespace_runner.py:84-90`), and the setns stdin payload
//! (`isolated_workspace/scripts/setns_exec.py:14-19`). The verb-specific `args`
//! stay an opaque [`serde_json::Value`] here (the runner forwards them verbatim
//! to the in-namespace tool primitive); the typed per-verb args/results live in
//! `eos_protocol::models`.

use std::os::unix::io::RawFd;
use std::path::PathBuf;

use eos_protocol::Intent;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Which namespace strategy the runner uses for this call.
///
/// `// PORT backend/src/sandbox/overlay/namespace_runner.py:48-71 — fresh vs existing dispatch`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunMode {
    /// Create a brand-new private namespace stack via `unshare`, mount the
    /// overlay, then exec — one tool call per namespace.
    /// `// PORT backend/src/sandbox/overlay/namespace_runner.py:72 — _run_tool_call_in_fresh_namespace`
    FreshNs,
    /// `setns` into the ns-holder's already-open namespace FDs, then exec.
    /// `// PORT backend/src/sandbox/overlay/namespace_runner.py:138 — _run_tool_call_in_existing_namespace`
    SetNs,
}

/// A raw file descriptor handle.
///
/// `#[repr(transparent)]` lets this cross the FFI boundary into the `setns(2)`
/// syscall unchanged.
/// `// PORT backend/src/sandbox/isolated_workspace/scripts/setns_exec.py:14-19 — ns_fds`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[repr(transparent)]
pub struct Fd(pub RawFd);

/// The validated workspace root the overlay is mounted at (e.g. `/testbed`).
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:21 — ISOLATED_WORKSPACE_ROOT`
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceRoot(pub PathBuf);

/// The ns-holder's pre-opened namespace FDs.
///
/// Applied in this exact order:
/// `user` (privilege change), `mnt` (mount table), `pid` (descendants only,
/// before `fork`), `net`. A wrong order breaks the setns sequence.
/// `// PORT backend/src/sandbox/isolated_workspace/scripts/setns_exec.py:54-66 — setns order`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct NsFds {
    /// User namespace FD (`CLONE_NEWUSER`) — applied first.
    pub user: Option<Fd>,
    /// Mount namespace FD (`CLONE_NEWNS`).
    pub mnt: Option<Fd>,
    /// PID namespace FD (`CLONE_NEWPID`) — affects descendants only; set before `fork`.
    pub pid: Option<Fd>,
    /// Network namespace FD (`CLONE_NEWNET`).
    pub net: Option<Fd>,
}

/// One tool invocation, the runner's view of `ToolCallRequest`.
///
/// `args` is the opaque verb payload forwarded to the in-namespace primitive;
/// `intent` reuses the protocol enum so the runner does not redefine the verb
/// taxonomy.
/// `// PORT backend/src/sandbox/shared/models.py:80-98 — ToolCallRequest.to_payload`
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ToolCall {
    pub invocation_id: String,
    pub agent_id: String,
    pub verb: String,
    pub intent: Intent,
    pub args: Value,
    #[serde(default)]
    pub background: bool,
}

/// A fully-resolved request to the runner: which mode, the tool call, the
/// overlay layout (fresh-ns), and the held namespace FDs (setns).
///
/// `// PORT backend/src/sandbox/overlay/namespace_runner.py:84-101 — fresh-ns request file`
/// `// PORT backend/src/sandbox/isolated_workspace/scripts/setns_exec.py:14-19 — setns stdin payload`
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunRequest {
    /// Fresh-ns vs setns.
    pub mode: RunMode,
    /// The tool invocation to execute inside the namespace.
    pub tool_call: ToolCall,
    /// Where the overlay is (or will be) mounted; the exec cwd / `/testbed`.
    pub workspace_root: WorkspaceRoot,
    /// Overlay lower layers (newest-first), present for [`RunMode::FreshNs`].
    /// `// PORT backend/src/sandbox/overlay/namespace_entrypoint.py:62-90 — _OverlayMountRequest`
    #[serde(default)]
    pub layer_paths: Vec<PathBuf>,
    /// Overlay upperdir (fresh-ns).
    #[serde(default)]
    pub upperdir: Option<PathBuf>,
    /// Overlay workdir (fresh-ns).
    #[serde(default)]
    pub workdir: Option<PathBuf>,
    /// Held namespace FDs to `setns` into, present for [`RunMode::SetNs`].
    #[serde(default)]
    pub ns_fds: Option<NsFds>,
    /// Absolute iws cgroup path; the setns child joins it before `fork` so the
    /// child inherits cgroup membership.
    /// `// PORT backend/src/sandbox/isolated_workspace/scripts/setns_exec.py:42-50 — cgroup join`
    #[serde(default)]
    pub cgroup_path: Option<PathBuf>,
    /// Hard timeout for the tool call (tool's own timeout + a fixed margin).
    /// `// PORT backend/src/sandbox/overlay/namespace_runner.py:217-222 — _tool_timeout +10s`
    #[serde(default)]
    pub timeout_seconds: Option<f64>,
}

/// The runner's result.
///
/// Contains the in-namespace tool result JSON plus the child's exit code. The
/// Python helpers return the tool primitive's `asdict` dict verbatim
/// (defaulting `workspace`), which the runner forwards opaquely as [`Value`].
/// `// PORT backend/src/sandbox/overlay/namespace_runner.py:192-205 — result forwarding`
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunResult {
    /// The tool primitive's result object (`SandboxResultBase`/`GuardedResultBase`
    /// `asdict`), forwarded unchanged.
    pub tool_result: Value,
    /// The child process exit code.
    pub exit_code: i32,
}
