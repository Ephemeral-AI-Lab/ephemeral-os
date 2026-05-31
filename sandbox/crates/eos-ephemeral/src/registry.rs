//! Process-local pipeline registry.
//!
//! Mirrors the Python `pipeline_registry`: a daemon-process LRU keyed by
//! `(layer_stack_root, workspace_root)` handing out one pipeline per binding,
//! with per-key locks so concurrent first callers share a single `start()`, and
//! a one-shot reap of stale runtime overlay scratch from a prior daemon process.
//!
//! The map/lock primitives the Python expresses with `OrderedDict` +
//! per-key `asyncio.Lock` are a daemon-side concern (the daemon owns the tokio
//! runtime); this skeleton fixes the constants, the key shape, and the accessor
//! signatures with `todo!()` bodies.

/// Max number of cached pipelines before LRU eviction.
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:34 — _MAX_PIPELINES`
pub const MAX_PIPELINES: usize = 256;

/// The LRU key shape: `"{layer_stack_root}\0{workspace_root}"` (posix paths).
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:55 — key`
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct PipelineKey {
    /// Canonicalized layer-stack root (posix).
    pub layer_stack_root: String,
    /// Effective workspace mount root (posix), validated against the binding.
    pub workspace_root: String,
}

impl PipelineKey {
    /// The flat NUL-joined cache key string the Python uses verbatim.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:55`
    pub fn as_cache_key(&self) -> String {
        format!("{}\0{}", self.layer_stack_root, self.workspace_root)
    }
}

/// Return (constructing if needed) the daemon-owned pipeline for a bound
/// workspace, optionally mounting its overlay (`start`).
///
/// Validates the workspace binding, keys the LRU, and shares a single `start()`
/// across concurrent first callers via the per-key lock.
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:40-77 — get_ephemeral_pipeline`
pub async fn get_ephemeral_pipeline(
    _layer_stack_root: &str,
    _workspace_root: Option<&str>,
    _start: bool,
) -> crate::error::Result<PipelineKey> {
    // PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:47-54 — require_workspace_binding + mismatch check
    // PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:56-77 — per-key lock, LRU get/insert/evict, start()
    todo!("PORT: bind+validate workspace, LRU get-or-create, conditional start()")
}

/// Stop and drop every cached pipeline.
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:80-85 — stop_all_ephemeral_pipelines`
pub async fn stop_all_ephemeral_pipelines() -> crate::error::Result<()> {
    todo!("PORT: drain the LRU, stop() each pipeline")
}

/// Stop every pipeline bound to `layer_stack_root` and unmount its workspace
/// candidates, returning the warnings/stopped-count summary.
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:88-124 — stop_ephemeral_pipeline`
pub async fn stop_ephemeral_pipeline(
    _layer_stack_root: &str,
    _workspace_root: Option<&str>,
) -> crate::error::Result<StopSummary> {
    todo!("PORT: pop matching cache entries, stop() each, unmount candidates")
}

/// Summary returned by [`stop_ephemeral_pipeline`].
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:119-124 — return dict`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StopSummary {
    /// Workspace roots that were unmount candidates.
    pub workspace_roots: Vec<String>,
    /// Count of pipelines successfully stopped.
    pub stopped_overlays: usize,
    /// Best-effort cleanup warnings.
    pub warnings: Vec<String>,
}

/// Remove per-call overlay scratch left behind by a previous daemon process.
/// Runs at most once per process (the `_STALE_RUNTIME_OVERLAYS_REAPED` latch).
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:148-177 — _reap_stale_runtime_overlay_dirs_once`
pub fn reap_stale_runtime_overlay_dirs_once() {
    // PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:155 — overlay_writable_root()/runtime/overlay
    // PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:156-177 — iterdir + rmtree/unlink best-effort
    todo!("PORT: one-shot reap of stale `runtime/overlay` scratch dirs")
}
