//! Black-box live E2E harness for EphemeralOS, driven through `sandbox-cli`.

pub mod assertion;
pub mod cli_client;
pub mod config;
pub mod fixtures;
pub mod gateway;

pub use cli_client::{CallRecord, CliClient};
pub use config::RunConfig;
pub use fixtures::{Harness, Sandbox};
