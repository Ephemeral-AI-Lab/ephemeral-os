#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/e2e-test/tests/pressure/config/default.test.yml";

mod helpers;
mod test_pressure_cross_mode_consistency;
mod test_pressure_multi_caller;
mod test_pressure_resource_report;
mod test_pressure_soak;
