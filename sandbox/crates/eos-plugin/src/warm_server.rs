//! Warm per-session plugin server + the registry that keys one server per
//! `layer_stack_root`.
//!
//! # Invariant (AV-3 warm-server teardown)
//!
//! The Python path re-imported the plugin module (`importlib.import_module`) per
//! call. The Rust path keeps a WARM server process per session, keyed by
//! `layer_stack_root`, reachable over the [PPC channel](crate::ppc). The server
//! holds the loaded plugin runtime (Node 22.13.1 + Pyright payload — provisioned
//! via `put_archive`, NOT a Cargo dependency of this crate) and the long-lived
//! `PluginService` sessions READ_ONLY ops query.
//!
//! AV-3: the warm server is TORN DOWN on session end. The registry owns the
//! handles; [`WarmServer`] tears the process group down on [`Drop`]. Because a
//! panic in `Drop` aborts the process under `panic="abort"`, the destructor does
//! a best-effort, non-panicking teardown — the real kill/reap logic lives in
//! [`WarmServer::teardown`] (a future port), and `Drop` only invokes it.
//!
//! `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:65-75 — _LOADED_PLUGIN_RUNTIMES / _WORKSPACE_PROJECTIONS per-root cache`
//! `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:212-243 — _unload_plugin_runtime / _evict_plugin_sessions teardown`

use std::collections::HashMap;

use crate::error::Result;

/// LRU cap on warm-server / projection entries, keyed per `layer_stack_root`.
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:68 — _MAX_WORKSPACE_PROJECTIONS = 256`
pub const MAX_WARM_SERVERS: usize = 256;

/// A warm per-session plugin server handle, keyed by `layer_stack_root`.
///
/// Owns the server child process group + its PPC endpoint. Tearing it down on
/// `Drop` is the AV-3 guarantee. Body fields are intentionally minimal at the
/// skeleton stage; the future port stores the child PID / socket path / loaded
/// op set here.
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:59-63 — _LoadedPluginRuntime`
#[derive(Debug)]
pub struct WarmServer {
    /// The `layer_stack_root` this server is keyed on (its session identity).
    pub layer_stack_root: String,
    /// Public op names this warm server has flushed into the dispatcher.
    pub registered_ops: Vec<String>,
    /// Whether [`teardown`](WarmServer::teardown) already ran (idempotence latch).
    torn_down: bool,
}

impl WarmServer {
    /// Tear down the warm server: SIGTERM/SIGKILL the server process group, drain
    /// the PPC channel, and evict the loaded plugin sessions. Idempotent. NOT a
    /// `Drop` body (a panic in `Drop` aborts under `panic="abort"`).
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:212-243 — _unload_plugin_runtime + _evict_plugin_sessions`
    pub fn teardown(&mut self) -> Result<()> {
        if self.torn_down {
            return Ok(());
        }
        self.torn_down = true;
        // PORT runtime_api.py:212-243 — kill server process group, close PPC
        //   socket, evict_all() plugin sessions, drop loaded op table entries.
        todo!(
            "PORT runtime_api.py:212-243 — kill warm server process group + evict plugin sessions"
        )
    }
}

impl Drop for WarmServer {
    fn drop(&mut self) {
        // Best-effort, non-panicking AV-3 teardown. The real kill/reap lives in
        // `teardown`; here we only attempt it and swallow the (future) error so a
        // failed teardown never aborts the process under `panic="abort"`.
        // PORT runtime_api.py:212-243 — best-effort warm-server reap on drop.
        if !self.torn_down {
            let _ = self.teardown();
        }
    }
}

/// Process-local registry of warm servers, ONE per `layer_stack_root`.
///
/// Mirrors the Python `_LOADED_PLUGIN_RUNTIMES` / `_WORKSPACE_PROJECTIONS`
/// per-root caches. A `get_or_spawn` shares a single warm server across calls for
/// the same root; eviction (LRU at [`MAX_WARM_SERVERS`], or explicit session end)
/// drops the handle, which tears the server down (AV-3).
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:65-75 — per-root caches`
#[derive(Debug, Default)]
pub struct WarmServerRegistry {
    servers: HashMap<String, WarmServer>,
}

impl WarmServerRegistry {
    /// A fresh, empty registry.
    pub fn new() -> Self {
        Self::default()
    }

    /// Return (spawning + warming if needed) the warm server for a session root.
    /// The importlib load path (`overlay_child.py:129`) is replaced here by a
    /// warm out-of-process server reachable over the PPC channel.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/overlay_child.py:129 — importlib.import_module -> PPC warm load`
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:293-308 — _workspace_projection_for_layer_stack_root get-or-create`
    pub fn get_or_spawn(&mut self, layer_stack_root: &str) -> Result<&mut WarmServer> {
        let _ = (&mut self.servers, layer_stack_root);
        // PORT overlay_child.py:129 — spawn warm server (Node 22.13.1 + Pyright
        //   payload via put_archive), open PPC AF_UNIX channel, warm the runtime.
        // PORT runtime_api.py:293-308 — LRU get-or-create keyed on layer_stack_root,
        //   evict-on-overflow (eviction Drops -> teardown, AV-3).
        todo!("PORT overlay_child.py:129 + runtime_api.py:293-308 — get-or-spawn warm server keyed by layer_stack_root")
    }

    /// End a session: drop the warm server for `layer_stack_root`, tearing it
    /// down (AV-3). Returns whether a server was present.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:212-221 — _unload_plugin_runtime`
    pub fn end_session(&mut self, layer_stack_root: &str) -> bool {
        // Dropping the removed `WarmServer` runs its `Drop` -> best-effort teardown.
        self.servers.remove(layer_stack_root).is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn teardown_is_idempotent_after_marking() {
        // Construct a handle that is already torn down so `teardown`/`Drop` don't
        // reach the `todo!()`; this asserts the idempotence latch only.
        let mut server = WarmServer {
            layer_stack_root: "/lsr".to_owned(),
            registered_ops: vec![],
            torn_down: true,
        };
        assert!(server.teardown().is_ok());
    }

    #[test]
    fn end_session_reports_absence() {
        let mut registry = WarmServerRegistry::new();
        assert!(!registry.end_session("/lsr"));
    }
}
