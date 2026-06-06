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
