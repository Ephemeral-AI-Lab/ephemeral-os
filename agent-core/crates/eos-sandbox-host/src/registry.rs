//! The provider registry as explicit application state (GC-02), plus the
//! Docker-only provider-selection resolver.
//!
//! `eos-runtime` builds one `Arc<ProviderRegistry>` at the composition root and
//! seeds the default provider there. The default binding is first-call-wins, so
//! no separate bootstrap singleton is needed.

use std::collections::HashMap;
use std::sync::Arc;

use eos_types::SandboxId;
use parking_lot::RwLock;

use crate::error::SandboxHostError;
use crate::provider::{ProviderAdapter, ProviderKind};

/// The sandbox provider kind. agent-core is Docker-only, and sandbox
/// configuration (including any provider selection) is owned by the ephemeral-os
/// sandbox module — so this is a fixed host-side constant, not central config.
#[must_use]
pub fn resolve_provider_kind() -> ProviderKind {
    ProviderKind::Docker
}

/// Process-local provider adapter registry, held as `Arc<ProviderRegistry>` and
/// seeded once at the composition root.
///
/// Two registration modes coexist (mirroring `registry.py`): a process-wide
/// `default` used before a sandbox id is minted, and per-sandbox `bindings` used
/// by instance-scoped operations. Reads dominate, so both live behind
/// `parking_lot::RwLock` (`own-rwlock-readers`); every read clones the `Arc` out
/// and drops the guard before any `.await` (`async-no-lock-await`).
#[derive(Debug, Default)]
pub struct ProviderRegistry {
    default: RwLock<Option<Arc<dyn ProviderAdapter>>>,
    bindings: RwLock<HashMap<SandboxId, Arc<dyn ProviderAdapter>>>,
}

impl ProviderRegistry {
    /// Construct an empty registry (no default, no bindings).
    #[must_use]
    pub fn new() -> Self {
        // Construct fields directly: the inherent `default(&self)` method below
        // shadows `Default::default()` for `Self::default()` path resolution.
        Self {
            default: RwLock::new(None),
            bindings: RwLock::new(HashMap::new()),
        }
    }

    /// Bind the process-wide default provider adapter. The first seed wins:
    /// repeat calls are no-ops, with a warning if a different provider kind tries
    /// to replace the live default.
    pub fn set_default(&self, adapter: Arc<dyn ProviderAdapter>) {
        let mut default = self.default.write();
        if let Some(existing) = default.as_ref() {
            if existing.kind() != adapter.kind() {
                tracing::warn!(
                    first = existing.kind().as_str(),
                    now = adapter.kind().as_str(),
                    "sandbox default provider already seeded; ignoring replacement"
                );
            }
            return;
        }
        *default = Some(adapter);
    }

    /// The process-wide default provider adapter, or [`SandboxHostError::NoDefaultProvider`].
    #[allow(clippy::should_implement_trait)] // spec §6 names this method `default`; distinct from `Default::default`.
    pub fn default(&self) -> Result<Arc<dyn ProviderAdapter>, SandboxHostError> {
        self.default
            .read()
            .clone()
            .ok_or(SandboxHostError::NoDefaultProvider)
    }

    /// Bind `id` to `adapter` in this orchestrator process.
    pub fn register(&self, id: &SandboxId, adapter: Arc<dyn ProviderAdapter>) {
        self.bindings.write().insert(id.clone(), adapter);
    }

    /// Whether `id` has an explicit binding. Stays `false` after a fallback
    /// lookup (WR-01).
    #[must_use]
    pub fn has(&self, id: &SandboxId) -> bool {
        self.bindings.read().contains_key(id)
    }

    /// The adapter for `id`: an explicit binding if present, else the default
    /// **without caching** the association (WR-01 / GC-06). When neither exists,
    /// [`SandboxHostError::UnknownSandbox`].
    pub fn adapter(&self, id: &SandboxId) -> Result<Arc<dyn ProviderAdapter>, SandboxHostError> {
        if let Some(adapter) = self.bindings.read().get(id) {
            return Ok(Arc::clone(adapter));
        }
        // WR-01: fall back to the default WITHOUT inserting into `bindings`, so
        // `has(id)` keeps reporting `false` and the cache cannot grow unbounded.
        self.default
            .read()
            .clone()
            .ok_or_else(|| SandboxHostError::UnknownSandbox(id.clone()))
    }

    /// Remove the binding for `id` if present.
    pub fn dispose(&self, id: &SandboxId) {
        self.bindings.write().remove(id);
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use crate::support::MockAdapter;

    fn sid(s: &str) -> SandboxId {
        s.parse().expect("non-empty id")
    }

    // AC-eos-sandbox-host-01: the Docker-only host resolves to Docker.
    #[test]
    fn resolves_docker_provider() {
        assert_eq!(resolve_provider_kind(), ProviderKind::Docker);
    }

    // AC-eos-sandbox-host-02: register+adapter returns the bound adapter;
    // adapter(unknown) returns the default and has(unknown) stays false (WR-01).
    #[test]
    fn fallback_does_not_cache() {
        let registry = ProviderRegistry::new();
        let default: Arc<dyn ProviderAdapter> = Arc::new(MockAdapter::new().with_id("default"));
        let bound: Arc<dyn ProviderAdapter> = Arc::new(MockAdapter::new().with_id("bound"));

        // No default yet → NoDefaultProvider / UnknownSandbox.
        assert!(matches!(
            registry.default(),
            Err(SandboxHostError::NoDefaultProvider)
        ));
        assert!(matches!(
            registry.adapter(&sid("ghost")),
            Err(SandboxHostError::UnknownSandbox(_))
        ));

        registry.set_default(Arc::clone(&default));
        registry.register(&sid("bound"), Arc::clone(&bound));

        // Explicit binding wins and is reported by `has`.
        assert!(registry.has(&sid("bound")));
        assert!(Arc::ptr_eq(
            &registry.adapter(&sid("bound")).unwrap(),
            &bound
        ));

        // Unknown id falls back to default WITHOUT caching.
        assert!(Arc::ptr_eq(
            &registry.adapter(&sid("unknown")).unwrap(),
            &default
        ));
        assert!(
            !registry.has(&sid("unknown")),
            "WR-01: fallback must not insert a binding"
        );

        // dispose removes the explicit binding.
        registry.dispose(&sid("bound"));
        assert!(!registry.has(&sid("bound")));
        assert!(Arc::ptr_eq(
            &registry.adapter(&sid("bound")).unwrap(),
            &default
        ));
    }

    #[test]
    fn default_seed_is_first_call_wins() {
        let registry = ProviderRegistry::new();
        let first: Arc<dyn ProviderAdapter> = Arc::new(MockAdapter::new().with_id("first"));
        let second: Arc<dyn ProviderAdapter> = Arc::new(MockAdapter::new().with_id("second"));

        registry.set_default(Arc::clone(&first));
        registry.set_default(second);

        assert!(Arc::ptr_eq(&registry.default().unwrap(), &first));
    }

    proptest::proptest! {
        // AC-02 (GC-06): no sequence of fallback lookups ever grows `bindings`.
        #[test]
        fn fallback_never_grows_bindings(ids in proptest::collection::vec("[a-z]{1,8}", 0..40)) {
            let registry = ProviderRegistry::new();
            registry.set_default(Arc::new(MockAdapter::new().with_id("default")));
            for id in &ids {
                let _ = registry.adapter(&sid(id));
            }
            for id in &ids {
                proptest::prop_assert!(!registry.has(&sid(id)));
            }
        }
    }
}
