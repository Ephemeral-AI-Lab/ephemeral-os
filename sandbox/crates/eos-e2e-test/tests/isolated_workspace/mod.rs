#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/eos-e2e-test/tests/isolated_workspace/config/default.test.yml";

mod command_sessions;
mod lifecycle;
mod network;
mod no_publish;
mod tool_routing;
