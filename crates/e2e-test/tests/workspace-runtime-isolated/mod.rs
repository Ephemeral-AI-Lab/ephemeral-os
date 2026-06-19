#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/e2e-test/tests/workspace-runtime-isolated/config/default.test.yml";

mod isolated_cross_mode_consistency;
mod isolated_daemon_restart;
mod isolated_isolation;
mod isolated_lifecycle;
