//! Protocol-only live sandbox E2E harness.
//!
//! This crate owns test infrastructure only. Docker lifecycle is allowed for
//! container bring-up; every sandbox operation under test must go through
//! `host` over the live daemon wire.

use std::path::Path;
#[cfg(feature = "e2e")]
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::Result;

pub mod cas;
pub mod container;
pub mod pool;
pub mod run;

pub mod client {
    pub use host::e2e_support::{
        decode_trace_sidecar_base64, response_fault_kind, response_is_accepted, response_status,
        take_trace_sidecar_checked, ClientError, ProtocolClient, TraceSidecarError,
        TraceWireContext, DAEMON_TRACE_SIDECAR_ENCODING, DAEMON_TRACE_SIDECAR_FIELD,
        DAEMON_TRACE_SIDECAR_SCHEMA,
    };
}

pub mod config {
    pub use config::configs::e2e_test::*;
    pub use config::ConfigDocument;
}

pub use pool::{NodeLease, NodePool, RecordedClient};

static INVOCATION_COUNTER: AtomicU64 = AtomicU64::new(1);
#[cfg(feature = "e2e")]
const DEFAULT_IMAGE_CONTAINER_CAP: usize = 10;

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

    static POOL: OnceLock<Result<LivePool, String>> = OnceLock::new();
    let config_path = config_path.as_ref();
    match POOL.get_or_init(|| load_live_pool(config_path)) {
        Ok(pool) if pool.config_path == config_path => Ok(Some(Arc::clone(&pool.pool))),
        Ok(pool) => anyhow::bail!(
            "e2e-test can merge at most one *.test.yml per test binary; already using {}, requested {}",
            pool.config_path.display(),
            config_path.display()
        ),
        Err(err) => anyhow::bail!("{err}"),
    }
}

#[cfg(not(feature = "e2e"))]
pub fn live_pool_with_config(_config_path: impl AsRef<Path>) -> Result<Option<Arc<NodePool>>> {
    Ok(None)
}

#[cfg(feature = "e2e")]
struct LivePool {
    config_path: PathBuf,
    pool: Arc<NodePool>,
}

#[cfg(feature = "e2e")]
fn load_live_pool(config_path: &Path) -> Result<LivePool, String> {
    try_load_live_pool(config_path)
        .map(|pool| LivePool {
            config_path: config_path.to_path_buf(),
            pool: Arc::new(pool),
        })
        .map_err(|err| format!("{err:#}"))
}

#[cfg(feature = "e2e")]
fn try_load_live_pool(config_path: &Path) -> Result<NodePool> {
    let (mut config, doc) = config::Config::load_test_override(config_path)?;
    let run = run::RunContext::new(config_path, &config.artifacts)?;
    config.workload.perf_artifact_dir = run.perf_dir().to_path_buf();
    pool_from_config_and_doc(config, &doc, run)
}

#[cfg(feature = "e2e")]
fn pool_from_config_and_doc(
    config: config::Config,
    doc: &config::ConfigDocument,
    run: run::RunContext,
) -> Result<NodePool> {
    if !container::docker_available() {
        anyhow::bail!("docker is required for e2e-test --features e2e");
    }
    if !config.eosd_path.is_file() {
        anyhow::bail!(
            "missing configured eosd binary at {}; build/package it before running live E2E",
            config.eosd_path.display()
        );
    }
    container::ensure_existing_image_container_cap(
        &config.image,
        configured_container_weight_cap(),
        run.run_id(),
        configured_root_run_id().as_deref(),
    )?;
    Ok(NodePool::new(config, doc.to_yaml_string()?, run))
}

#[cfg(feature = "e2e")]
fn configured_container_weight_cap() -> usize {
    std::env::var("EOS_E2E_CONTAINER_WEIGHT_CAP")
        .ok()
        .and_then(|value| value.parse().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_IMAGE_CONTAINER_CAP)
}

#[cfg(feature = "e2e")]
fn configured_root_run_id() -> Option<String> {
    std::env::var("EOS_E2E_ROOT_RUN_ID")
        .ok()
        .filter(|value| !value.trim().is_empty())
}
