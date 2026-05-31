//! The per-call plugin op context — a THIN carrier of call identity.
//!
//! Deliberately thin: the layer-stack snapshot/lease HINGE and the OCC single
//! writer are CONSUMED as injected ports by the [dispatch](crate::dispatch)
//! functions ([`eos_layerstack::SnapshotLeasePort`] +
//! [`eos_ephemeral::OccRuntimeServicesPort`]), NOT redefined here. This context
//! only carries the identity/intent the warm-server call and audit need.
//!
//! The Python `PluginOpContext.projection` is the layer-stack HINGE
//! (`projection.py:10` -> `LayerStackPortAdapter`, used for snapshot/lease +
//! projection, NEVER publish, NEVER occ). That surface lives in `eos-layerstack`
//! ([`eos_layerstack::SnapshotLeasePort`]) — which is exactly why this crate links
//! `eos-layerstack` and never `eos-occ`.
//! `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_context.py:80-97 — PluginOpContext`
//! `// PORT backend/src/sandbox/ephemeral_workspace/plugin/projection.py:10 — HINGE (snapshot/lease/projection, never publish)`

use eos_protocol::Intent;

/// The audit identity of a plugin op caller. Minimal: `eos_protocol` does not
/// export a `SandboxCaller`, so this carries only the agent identity the warm
/// server + audit need (the full caller-field set is a daemon-side concern).
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_context.py:37-47 — sandbox_caller_from_plugin_envelope`
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PluginCaller {
    /// The dispatching agent id (the isolated-mode + audit key).
    pub agent_id: String,
}

/// Per-call context handed to a plugin op dispatch. References to the injected
/// HINGE / single-writer ports are passed alongside this by the dispatch fns;
/// this struct holds only the call's identity + intent + free-form metadata.
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_context.py:80-97 — PluginOpContext`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PluginOpContext {
    /// The session key (also the warm-server registry + MF-1 single-writer key).
    pub layer_stack_root: String,
    /// Caller identity for audit + the isolated-mode guard.
    pub caller: PluginCaller,
    /// The intent that selected the dispatch mode.
    pub intent: Intent,
}

impl PluginOpContext {
    /// Build a context, defaulting `intent` to `ReadOnly` (the Python default).
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_context.py:50-57 — plugin_intent_from_envelope`
    pub fn new(layer_stack_root: impl Into<String>, caller: PluginCaller, intent: Intent) -> Self {
        Self {
            layer_stack_root: layer_stack_root.into(),
            caller,
            intent,
        }
    }
}
