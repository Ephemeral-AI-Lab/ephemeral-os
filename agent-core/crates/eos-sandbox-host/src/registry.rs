//! The provider registry as explicit application state (GC-02), plus the
//! Docker-only provider-selection resolver (folded in from `bootstrap.py`).
//!
//! Replaces the Python `provider/registry.py` module globals + the
//! `bootstrap.py` first-call-wins sentinel with an `Arc<ProviderRegistry>` built
//! and seeded once at the `eos-runtime` composition root.

use std::collections::HashMap;
use std::sync::Arc;

use eos_config::{SandboxConfig, SandboxProvider};
use eos_types::SandboxId;
use parking_lot::RwLock;

use crate::error::SandboxHostError;
use crate::provider::{ProviderAdapter, ProviderKind};

/// Resolve the sandbox provider kind from the optional `EOS_SANDBOX_PROVIDER`
/// override and the central config, failing fast on a non-Docker value
/// (`api-parse-dont-validate`, GC-02). `env_override` is the resolved value of
/// `EOS_SANDBOX_PROVIDER` (the `eos-runtime` composition root reads the process
/// env); `None` falls back to `config.default_provider`.
///
/// Mirrors `bootstrap.py::_resolve_provider_name` (strip + lowercase) plus the
/// validity check; the Python sentinel/first-call-wins global is dropped (the
/// registry app state replaces it).
pub fn resolve_provider_kind(
    env_override: Option<&str>,
    config: &SandboxConfig,
) -> Result<ProviderKind, SandboxHostError> {
    match env_override {
        Some(raw) => match raw.trim().to_ascii_lowercase().as_str() {
            "docker" => Ok(ProviderKind::Docker),
            other => Err(SandboxHostError::UnknownProviderKind(other.to_owned())),
        },
        None => match config.default_provider {
            SandboxProvider::Docker => Ok(ProviderKind::Docker),
            // `SandboxProvider` is `#[non_exhaustive]`; agent-core is Docker-only,
            // so any future variant fails fast here.
            other => Err(SandboxHostError::UnknownProviderKind(format!("{other:?}"))),
        },
    }
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

    /// Bind the process-wide default provider adapter (idempotent overwrite).
    pub fn set_default(&self, adapter: Arc<dyn ProviderAdapter>) {
        *self.default.write() = Some(adapter);
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
    use crate::testutil::MockAdapter;

    fn sid(s: &str) -> SandboxId {
        s.parse().expect("non-empty id")
    }

    // AC-eos-sandbox-host-01: provider selection from config/env resolves to
    // Docker, and any non-Docker value returns UnknownProviderKind.
    #[test]
    fn selects_provider_from_config() {
        let config = SandboxConfig::default();
        // env override "docker" (any case / whitespace) → Docker.
        assert_eq!(
            resolve_provider_kind(Some(" Docker "), &config).unwrap(),
            ProviderKind::Docker
        );
        // env unset → falls back to config (Docker).
        assert_eq!(
            resolve_provider_kind(None, &config).unwrap(),
            ProviderKind::Docker
        );
        // a non-Docker env value fails fast.
        let err = resolve_provider_kind(Some("daytona"), &config).unwrap_err();
        assert!(matches!(
            err,
            SandboxHostError::UnknownProviderKind(k) if k == "daytona"
        ));
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
        assert!(Arc::ptr_eq(&registry.adapter(&sid("bound")).unwrap(), &bound));

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
        assert!(Arc::ptr_eq(&registry.adapter(&sid("bound")).unwrap(), &default));
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
