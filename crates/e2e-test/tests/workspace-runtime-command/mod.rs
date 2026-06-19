#[path = "../support/mod.rs"]
mod support;

const E2E_CONFIG: &str = "crates/e2e-test/tests/workspace-runtime-command/config/default.test.yml";

mod command_command_matrix;
mod command_external_process_death;
mod command_host_workspace;
mod command_isolated;
mod command_lifecycle;
mod command_local_os_sandbox;
