#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/eos-e2e-test/tests/isolated_workspace/config/default.test.yml";

mod test_isolated_workspace_command_sessions;
mod test_isolated_workspace_lifecycle;
mod test_isolated_workspace_network_isolation;
mod test_isolated_workspace_private_no_publish;
mod test_isolated_workspace_tool_routing;
