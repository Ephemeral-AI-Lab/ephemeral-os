//! Typed schemas for sections of `sandbox/config/prd.yml`.

#[path = "command-session.rs"]
pub mod command_session;

pub mod daemon;

#[path = "e2e-test.rs"]
pub mod e2e_test;

#[path = "isolated-workspace.rs"]
pub mod isolated_workspace;

pub mod runner;
