#![allow(clippy::unwrap_used)]
use super::*;
use crate::support::MockAdapter;

// AC-eos-sandbox-host-01: the Docker-only host resolves to Docker.
#[test]
fn resolves_docker_provider() {
    assert_eq!(resolve_provider_kind(), ProviderKind::Docker);
}

// `adapter()` errors before the registry is seeded, then returns the one adapter.
#[test]
fn adapter_requires_seed() {
    let registry = ProviderRegistry::new();
    assert!(matches!(
        registry.adapter(),
        Err(SandboxHostError::NoDefaultProvider)
    ));

    let adapter: Arc<dyn ProviderAdapter> = Arc::new(MockAdapter::new().with_id("docker"));
    registry.set_default(Arc::clone(&adapter));
    assert!(Arc::ptr_eq(&registry.adapter().unwrap(), &adapter));
}

// One adapter serves every sandbox: repeated lookups all return the same handle,
// regardless of how many sandboxes are in flight.
#[test]
fn one_adapter_serves_all_sandboxes() {
    let registry = ProviderRegistry::new();
    let adapter: Arc<dyn ProviderAdapter> = Arc::new(MockAdapter::new().with_id("docker"));
    registry.set_default(Arc::clone(&adapter));
    for _ in 0..5 {
        assert!(Arc::ptr_eq(&registry.adapter().unwrap(), &adapter));
    }
}

// Seeding is first-call-wins.
#[test]
fn seed_is_first_call_wins() {
    let registry = ProviderRegistry::new();
    let first: Arc<dyn ProviderAdapter> = Arc::new(MockAdapter::new().with_id("first"));
    let second: Arc<dyn ProviderAdapter> = Arc::new(MockAdapter::new().with_id("second"));

    registry.set_default(Arc::clone(&first));
    registry.set_default(second);

    assert!(Arc::ptr_eq(&registry.adapter().unwrap(), &first));
}
