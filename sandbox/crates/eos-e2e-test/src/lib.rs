//! Protocol-only live sandbox E2E harness.
//!
//! This crate owns test infrastructure only. Docker lifecycle is allowed for
//! container bring-up; every sandbox operation under test must go through
//! `eos-protocol` over the live daemon wire.

use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::Result;

pub mod audit;
pub mod cas;
pub mod client;
pub mod config;
pub mod container;
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

/// Return a live pool backed by one test-local config override.
///
/// # Errors
/// Returns an error when live execution is requested but the environment cannot
/// start Docker containers, locate the configured `eosd` binary, or load the
/// hardcoded test override.
#[cfg(feature = "e2e")]
pub fn live_pool_with_config(config_path: impl AsRef<Path>) -> Result<Option<Arc<NodePool>>> {
    use std::sync::OnceLock;

    static POOL: OnceLock<Result<Arc<NodePool>, String>> = OnceLock::new();
    let config_path = config_path.as_ref();
    match POOL.get_or_init(|| load_live_pool(config_path)) {
        Ok(pool) => Ok(Some(Arc::clone(pool))),
        Err(err) => anyhow::bail!("{err}"),
    }
}

#[cfg(not(feature = "e2e"))]
pub fn live_pool_with_config(_config_path: impl AsRef<Path>) -> Result<Option<Arc<NodePool>>> {
    Ok(None)
}

#[cfg(feature = "e2e")]
fn load_live_pool(config_path: &Path) -> Result<Arc<NodePool>, String> {
    try_load_live_pool(config_path)
        .map(Arc::new)
        .map_err(|err| format!("{err:#}"))
}

#[cfg(feature = "e2e")]
fn try_load_live_pool(config_path: &Path) -> Result<NodePool> {
    let (config, doc) = config::Config::load_test_override(config_path)?;
    pool_from_config_and_doc(config, &doc)
}

#[cfg(feature = "e2e")]
fn pool_from_config_and_doc(
    config: config::Config,
    doc: &eos_config::ConfigDocument,
) -> Result<NodePool> {
    if !container::docker_available() {
        anyhow::bail!("docker is required for eos-e2e-test --features e2e");
    }
    if !config.eosd_path.is_file() {
        anyhow::bail!(
            "missing configured eosd binary at {}; build/package it before running live E2E",
            config.eosd_path.display()
        );
    }
    Ok(NodePool::new(config, doc.to_yaml_string()?))
}
