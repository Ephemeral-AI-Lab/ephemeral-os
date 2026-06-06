#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/eos-e2e-test/tests/eos-occ/config/default.test.yml";

mod test_eos_occ_merge_conflicts_and_publish;
mod test_eos_occ_route_gating;
