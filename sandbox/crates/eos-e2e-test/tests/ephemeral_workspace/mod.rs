#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/eos-e2e-test/tests/ephemeral_workspace/config/default.test.yml";

mod test_ephemeral_workspace_command_sessions;
mod test_ephemeral_workspace_overlay_exec;
