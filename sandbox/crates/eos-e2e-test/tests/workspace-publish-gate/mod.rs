#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/eos-e2e-test/tests/workspace-publish-gate/config/default.test.yml";

mod workspace_publish_concurrent_contention;
mod workspace_publish_merge_conflicts;
mod workspace_publish_route_gating;
