#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/e2e-test/tests/workspace-runtime-command/config/default.test.yml";

mod command_cancel_runs;
mod command_command_matrix;
mod command_ephemeral_workspace;
mod command_error_and_backpressure;
mod command_external_process_death;
mod command_isolated_workspace;
mod command_lifecycle;
mod command_local_os_sandbox;
mod command_protocol_smoke;
