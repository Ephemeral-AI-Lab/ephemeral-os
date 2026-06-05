#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/eos-e2e-test/tests/layerstack/config/default.test.yml";

mod test_layerstack_git_overlay_commit;
mod test_layerstack_workspace_commit;
mod test_layerstack_lease_and_squash_pinning;
mod test_layerstack_squash_integrity;
mod test_layerstack_squash_bounds_and_cleanup;
mod test_layerstack_deep_squash_storage;
