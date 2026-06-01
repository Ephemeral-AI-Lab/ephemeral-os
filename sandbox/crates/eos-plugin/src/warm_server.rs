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

use std::collections::{HashMap, VecDeque};

use crate::error::{PluginError, Result};

/// LRU cap on warm-server / projection entries, keyed per `layer_stack_root`.
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:68 — _MAX_WORKSPACE_PROJECTIONS = 256`
pub const MAX_WARM_SERVERS: usize = 256;

/// A warm per-session plugin server handle, keyed by `layer_stack_root`.
///
/// Owns the server child process group + its PPC endpoint. Tearing it down on
/// `Drop` is the AV-3 guarantee. Because plugin PPC execution is deferred, this
/// handle currently stores only the session identity and registered op set; the
/// process-backed port will add the child PID / socket path here.
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
    /// Build the local handle for one layer-stack-root keyed warm server. The
    /// process-backed port fills in the child/PPC endpoint fields here; the
    /// registry semantics are already real so callers do not depend on a stub.
    pub fn new(layer_stack_root: impl Into<String>) -> Self {
        Self {
            layer_stack_root: layer_stack_root.into(),
            registered_ops: Vec::new(),
            torn_down: false,
        }
    }

    /// Tear down the warm server: SIGTERM/SIGKILL the server process group, drain
    /// the PPC channel, and evict the loaded plugin sessions. Idempotent. NOT a
    /// `Drop` body (a panic in `Drop` aborts under `panic="abort"`).
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:212-243 — _unload_plugin_runtime + _evict_plugin_sessions`
    pub fn teardown(&mut self) -> Result<()> {
        if self.torn_down {
            return Ok(());
        }
        self.torn_down = true;
        // Plugin PPC execution is deferred, so no child process handle exists
        // yet. The process-backed port will kill/reap the warm-server group and
        // close the PPC socket here.
        Ok(())
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
    lru: VecDeque<String>,
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
        let layer_stack_root = layer_stack_root.trim();
        if layer_stack_root.is_empty() {
            return Err(PluginError::Ensure(
                "layer_stack_root is required for plugin warm server".to_owned(),
            ));
        }
        if self.servers.contains_key(layer_stack_root) {
            self.touch(layer_stack_root);
            return self
                .servers
                .get_mut(layer_stack_root)
                .ok_or_else(|| PluginError::Ensure("warm server cache lookup failed".to_owned()));
        }
        if self.servers.len() >= MAX_WARM_SERVERS {
            self.evict_oldest();
        }
        self.servers.insert(
            layer_stack_root.to_owned(),
            WarmServer::new(layer_stack_root),
        );
        self.lru.push_back(layer_stack_root.to_owned());
        self.servers
            .get_mut(layer_stack_root)
            .ok_or_else(|| PluginError::Ensure("warm server cache insert failed".to_owned()))
    }

    /// End a session: drop the warm server for `layer_stack_root`, tearing it
    /// down (AV-3). Returns whether a server was present.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:212-221 — _unload_plugin_runtime`
    pub fn end_session(&mut self, layer_stack_root: &str) -> bool {
        // Dropping the removed `WarmServer` runs its `Drop` -> best-effort teardown.
        self.lru.retain(|key| key != layer_stack_root);
        self.servers.remove(layer_stack_root).is_some()
    }

    /// Number of warm servers currently retained.
    pub fn len(&self) -> usize {
        self.servers.len()
    }

    /// Whether the registry has no retained warm servers.
    pub fn is_empty(&self) -> bool {
        self.servers.is_empty()
    }

    fn touch(&mut self, layer_stack_root: &str) {
        self.lru.retain(|key| key != layer_stack_root);
        self.lru.push_back(layer_stack_root.to_owned());
    }

    fn evict_oldest(&mut self) {
        while let Some(key) = self.lru.pop_front() {
            if self.servers.remove(&key).is_some() {
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn teardown_is_idempotent_after_marking() {
        let mut server = WarmServer::new("/lsr");
        assert!(server.teardown().is_ok());
        assert!(server.teardown().is_ok());
    }

    #[test]
    fn end_session_reports_absence() {
        let mut registry = WarmServerRegistry::new();
        assert!(!registry.end_session("/lsr"));
    }

    #[test]
    fn get_or_spawn_reuses_one_server_per_layer_stack_root() {
        let mut registry = WarmServerRegistry::new();
        registry
            .get_or_spawn("/lsr")
            .expect("spawn warm server")
            .registered_ops
            .push("plugin.lsp.hover".to_owned());

        let server = registry
            .get_or_spawn("/lsr")
            .expect("reuse warm server for root");
        assert_eq!(server.registered_ops, vec!["plugin.lsp.hover".to_owned()]);
        assert_eq!(registry.len(), 1);
    }

    #[test]
    fn get_or_spawn_rejects_empty_root() {
        let mut registry = WarmServerRegistry::new();
        assert!(matches!(
            registry.get_or_spawn("  "),
            Err(PluginError::Ensure(message)) if message.contains("layer_stack_root")
        ));
    }

    #[test]
    fn registry_evicts_oldest_server_at_cap() {
        let mut registry = WarmServerRegistry::new();
        for index in 0..MAX_WARM_SERVERS {
            registry
                .get_or_spawn(&format!("/lsr-{index}"))
                .expect("fill warm server registry");
        }
        registry
            .get_or_spawn("/lsr-extra")
            .expect("insert with eviction");

        assert_eq!(registry.len(), MAX_WARM_SERVERS);
        assert!(!registry.servers.contains_key("/lsr-0"));
        assert!(registry.servers.contains_key("/lsr-extra"));
    }

    #[test]
    fn cache_hit_refreshes_lru_position() {
        let mut registry = WarmServerRegistry::new();
        for index in 0..MAX_WARM_SERVERS {
            registry
                .get_or_spawn(&format!("/lsr-{index}"))
                .expect("fill warm server registry");
        }
        registry.get_or_spawn("/lsr-0").expect("touch first entry");
        registry
            .get_or_spawn("/lsr-extra")
            .expect("insert with eviction");

        assert!(registry.servers.contains_key("/lsr-0"));
        assert!(!registry.servers.contains_key("/lsr-1"));
    }
}
