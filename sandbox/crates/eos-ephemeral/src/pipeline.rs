//! The per-operation collaborative pipeline orchestrator.
//!
//! INVARIANT: capture + publish are ATOMIC. A `WRITE_ALLOWED` overlay op
//! captures its upperdir changes and publishes them through the single OCC
//! writer in one commit boundary; a partial capture is never published. The fast
//! path skips the overlay entirely and commits directly through OCC.
//!
//! The orchestrator is `async` because in the daemon it runs inside the tokio
//! runtime — but this crate adds NO tokio dependency. Concurrency control that
//! the Python expresses with `asyncio.Lock` (`_operation_lock`,
//! `_shell_mount_maintenance_lock`) and the foreign-publish watcher task is a
//! FUTURE concern for the daemon-side implementer; it is intentionally not
//! modeled by a sync primitive here.

use eos_protocol::{
    EditFileResult, GlobResult, GrepResult, Intent, LayerChange, ReadFileResult, ShellResult,
    WriteFileResult,
};

use crate::dispatch::{DispatchRoute, OperationRequest, Verb};
use crate::error::Result;
use crate::ports::{
    ChangesetProjectionPort, LayerStackSnapshotPort, NamespaceRunnerPort, OccRuntimeServicesPort,
    OverlayHandle, OverlayLifecyclePort,
};

/// Default sandbox workspace mount point.
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:72 — workspace_root="/testbed"`
pub const DEFAULT_WORKSPACE_ROOT: &str = "/testbed";

/// Default shell pre-mount squash depth (collapse deep manifests before mount).
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:455-462 — EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH`
pub const DEFAULT_SHELL_MOUNT_SQUASH_MAX_DEPTH: u32 = 64;

/// Default foreign-publish watch interval, seconds.
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:481-488 — EOS_OVERLAY_FOREIGN_WATCH_INTERVAL_S`
pub const DEFAULT_FOREIGN_WATCH_INTERVAL_S: f64 = 0.25;

/// The daemon-owned per-binding pipeline: a facade over overlay freshness,
/// per-op capture, and OCC publish behind the daemon boundary.
///
/// Generic over the four injected ports so the daemon supplies real
/// implementations while this crate links none of them concretely. The fields
/// mirror `EphemeralPipeline.__init__`.
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:55-98 — EphemeralPipeline`
#[derive(Debug)]
pub struct EphemeralPipeline<L, O, R, P>
where
    L: LayerStackSnapshotPort,
    O: OccRuntimeServicesPort + OverlayLifecyclePort,
    R: NamespaceRunnerPort,
    P: ChangesetProjectionPort,
{
    layer_stack: L,
    services: O,
    runner: R,
    projection: P,
    workspace_ref: String,
    workspace_root: String,
}

impl<L, O, R, P> EphemeralPipeline<L, O, R, P>
where
    L: LayerStackSnapshotPort,
    O: OccRuntimeServicesPort + OverlayLifecyclePort,
    R: NamespaceRunnerPort,
    P: ChangesetProjectionPort,
{
    /// Construct a pipeline bound to one `(layer_stack_root, workspace_root)`.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:66-98 — __init__`
    pub fn new(
        layer_stack: L,
        services: O,
        runner: R,
        projection: P,
        workspace_ref: impl Into<String>,
        workspace_root: impl Into<String>,
    ) -> Self {
        let workspace_root = workspace_root.into();
        let workspace_root = workspace_root.trim_end_matches('/');
        let workspace_root = if workspace_root.is_empty() {
            "/".to_owned()
        } else {
            workspace_root.to_owned()
        };
        Self {
            layer_stack,
            services,
            runner,
            projection,
            workspace_ref: workspace_ref.into(),
            workspace_root,
        }
    }

    /// The bound workspace mount point.
    pub fn workspace_root(&self) -> &str {
        &self.workspace_root
    }

    /// The bound layer-stack reference (the registry key root).
    pub fn workspace_ref(&self) -> &str {
        &self.workspace_ref
    }

    /// Run one foreground tool call, routing it to the fast path or the overlay
    /// pipeline. This is the single entry the daemon dispatcher calls.
    ///
    /// The per-agent dispatch drain-gate
    /// ([`ChangesetProjectionPort::acquire_dispatch_slot`]) is acquired by the
    /// daemon dispatcher ABOVE this call, NOT here: the quiesce state is shared
    /// across the ephemeral AND isolated pipelines so `exit_isolated_workspace`
    /// can drain both before mutating routing state. Gating inside one pipeline
    /// would leave the other outside the same gate.
    /// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:251-257 — acquire_dispatch_slot wraps both pipelines`
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:130 — run_tool_call`
    pub async fn run_tool_call(&self, req: &OperationRequest) -> Result<OperationOutcome> {
        match req.route() {
            DispatchRoute::OccFastPath => self.run_fast_path(req).await,
            DispatchRoute::OverlayPipeline => self.run_overlay_path(req).await,
        }
    }

    /// OCC fast path: read/write/edit go straight to the layer stack / OCC
    /// writer with no overlay mount and no namespace.
    /// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:286-299 — _dispatch_layer_stack_file_request`
    async fn run_fast_path(&self, req: &OperationRequest) -> Result<OperationOutcome> {
        match &req.verb {
            Verb::ReadFile(_args) => {
                // PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:302-318 — _read_file_from_layer_stack
                let _ = &self.layer_stack;
                todo!("PORT: read_file fast path via LayerStackSnapshotPort::read_text")
            }
            Verb::WriteFile(_args) => {
                // PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:321-363 — _write_file_to_layer_stack
                let _ = &self.services;
                let _ = &self.projection;
                todo!("PORT: write_file fast path -> OccRuntimeServicesPort::apply_changeset + projection")
            }
            Verb::EditFile(_args) => {
                // PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:366-387 — _edit_file_in_layer_stack
                todo!("PORT: edit_file fast path -> build EditChange list + apply_changeset + projection")
            }
            // The overlay verbs never reach the fast path (routed by Verb::route).
            Verb::Shell(_) | Verb::Glob(_) | Verb::Grep(_) => self.run_overlay_path(req).await,
        }
    }

    /// Overlay pipeline: shell/glob/grep mount a fresh overlay over the leased
    /// snapshot, run inside a namespace via eos-runner, and (for WRITE_ALLOWED
    /// only) capture upperdir changes and publish them atomically.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:130-202 — run_tool_call overlay body`
    async fn run_overlay_path(&self, req: &OperationRequest) -> Result<OperationOutcome> {
        // PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:136-137 — shell pre-mount squash maintenance
        // PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:138-142 — overlay_lifecycle.acquire(...)
        // PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:146 — run_in_namespace(handle, req)
        // PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:147-187 — WRITE_ALLOWED capture+commit via capture_and_publish (atomic)
        // PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:201-202 — finally: release lease
        let _ = (&self.runner, &self.workspace_root, req.verb.intent());
        todo!(
            "PORT: overlay path acquire -> run_in_namespace -> capture_and_publish (WRITE_ALLOWED)"
        )
    }

    /// Capture upperdir changes and publish them through the single OCC writer in
    /// one atomic commit. Split out because it is the invariant-bearing step;
    /// the overlay arm calls this for `WRITE_ALLOWED` ops.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:149-163 — capture_changes + _commit_and_attach`
    pub(crate) async fn capture_and_publish(
        &self,
        handle: &OverlayHandle,
        intent: Intent,
    ) -> Result<Vec<LayerChange>> {
        debug_assert!(intent == Intent::WriteAllowed);
        // PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:149 — overlay_lifecycle.capture_changes(handle)
        // PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:158-163 — _commit_and_attach (publish)
        let _ = (handle, &self.services);
        todo!("PORT: capture upperdir changes then publish atomically via OCC")
    }
}

/// The result of one dispatched operation, in its verb-specific guarded shape.
///
/// Each arm is the corresponding `eos_protocol` result model; the daemon
/// serializes it back onto the wire. `Read` is the fast-path read result;
/// `Write`/`Edit` are the guarded fast-path results; `Shell`/`Glob`/`Grep` are
/// the overlay-path results.
/// `// PORT backend/src/sandbox/shared/models.py — ToolCallResult union`
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub enum OperationOutcome {
    /// `read_file` outcome.
    Read(ReadFileResult),
    /// `write_file` outcome.
    Write(WriteFileResult),
    /// `edit_file` outcome.
    Edit(EditFileResult),
    /// `shell` outcome.
    Shell(ShellResult),
    /// `glob` outcome.
    Glob(GlobResult),
    /// `grep` outcome.
    Grep(GrepResult),
}
