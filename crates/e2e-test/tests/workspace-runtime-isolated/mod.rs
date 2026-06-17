#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/e2e-test/tests/workspace-runtime-isolated/config/default.test.yml";

mod isolated_network_compact_remount;
mod isolated_network_cross_mode_consistency;
mod isolated_network_daemon_restart;
mod isolated_network_lifecycle;
mod isolated_network_network_isolation;
mod isolated_network_private_no_publish;
mod isolated_network_tool_routing;
