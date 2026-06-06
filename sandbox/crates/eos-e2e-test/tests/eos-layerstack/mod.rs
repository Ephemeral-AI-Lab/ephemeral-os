#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/eos-e2e-test/tests/eos-layerstack/config/default.test.yml";

mod test_eos_layerstack_deep_squash_storage;
mod test_eos_layerstack_git_overlay_commit;
mod test_eos_layerstack_lease_and_squash_pinning;
mod test_eos_layerstack_squash_bounds_and_cleanup;
mod test_eos_layerstack_squash_integrity;
mod test_eos_layerstack_workspace_commit;
