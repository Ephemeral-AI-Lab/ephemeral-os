#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/e2e-test/tests/core/config/default.test.yml";

mod test_core_error_catalog_and_limits;
mod test_core_protocol_wire_message_guards;
