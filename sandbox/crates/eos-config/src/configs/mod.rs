//! Typed schemas for sections of `sandbox/config/prd.yml`.

pub mod command;

pub mod daemon;

pub mod validate;

#[path = "e2e-test.rs"]
pub mod e2e_test;

#[path = "isolated-workspace.rs"]
pub mod isolated_workspace;

pub mod runner;
