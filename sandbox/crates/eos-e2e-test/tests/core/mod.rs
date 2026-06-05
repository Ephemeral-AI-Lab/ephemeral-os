#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/eos-e2e-test/tests/core/config/default.test.yml";

mod command_sessions;
mod direct_file_ops;
mod envelope_contract;
mod errors_and_limits;
mod runtime_setup;
mod smoke_paths;
