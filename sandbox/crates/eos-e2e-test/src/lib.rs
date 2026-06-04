//! Protocol-only live sandbox E2E harness.
//!
//! This crate owns test infrastructure only. Docker lifecycle is allowed for
//! container bring-up; every sandbox operation under test must go through
//! `eos-protocol` over the live daemon wire.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::Result;

pub mod audit;
pub mod cas;
pub mod client;
pub mod config;
pub mod container;
pub mod fixtures;
pub mod pool;

pub use pool::{NodeLease, NodePool};

static INVOCATION_COUNTER: AtomicU64 = AtomicU64::new(1);

/// A short process-local suffix for container, agent, and invocation ids.
#[must_use]
pub fn unique_suffix() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    let seq = INVOCATION_COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("{}-{nanos:x}-{seq:x}", std::process::id())
}

/// A fresh invocation id suitable for daemon calls.
#[must_use]
pub fn next_invocation_id() -> String {
    format!("eos-e2e-{}", unique_suffix())
}

/// Return a live pool when the `e2e` feature is enabled.
///
/// Without `--features e2e`, live tests call this and skip cleanly. With the
/// feature enabled, missing Docker or missing `eosd` is a hard failure.
///
/// # Errors
/// Returns an error when live execution is requested but the environment cannot
/// start Docker containers or locate the configured `eosd` binary.
#[cfg(feature = "e2e")]
pub fn live_pool() -> Result<Option<NodePool>> {
    let config = config::Config::load()?;
    if !container::docker_available() {
        anyhow::bail!("docker is required for eos-e2e-test --features e2e");
    }
    if !config.eosd_path.is_file() {
        anyhow::bail!(
            "missing eosd binary at {}; build/package it or set EOS_E2E_EOSD",
            config.eosd_path.display()
        );
    }
    Ok(Some(NodePool::new(config)))
}

/// Return no live pool unless the `e2e` feature is enabled.
///
/// # Errors
/// This non-live path does not fail.
#[cfg(not(feature = "e2e"))]
pub fn live_pool() -> Result<Option<NodePool>> {
    Ok(None)
}
