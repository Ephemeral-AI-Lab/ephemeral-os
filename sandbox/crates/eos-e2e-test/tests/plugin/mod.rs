#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/eos-e2e-test/tests/plugin/config/default.test.yml";

mod test_plugin_isolated_workspace_gate;
mod test_plugin_lsp_dispatch;
mod test_plugin_package_lifecycle_and_overlay;
