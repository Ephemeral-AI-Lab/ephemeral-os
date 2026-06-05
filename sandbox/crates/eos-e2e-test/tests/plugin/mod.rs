#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/eos-e2e-test/tests/plugin/config/default.test.yml";

mod isolated_gate;
mod lsp;
mod packages;
