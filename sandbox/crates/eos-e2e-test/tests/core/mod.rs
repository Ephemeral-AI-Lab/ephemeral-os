#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/eos-e2e-test/tests/core/config/default.test.yml";

mod test_core_command_session_lifecycle;
mod test_core_direct_file_contracts;
mod test_core_protocol_envelope_guards;
mod test_core_error_catalog_and_limits;
mod test_core_runtime_readiness_and_base;
mod test_core_protocol_smoke_paths;
