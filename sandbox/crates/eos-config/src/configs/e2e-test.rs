//! Topology/runtime config for the Rust E2E harness.
//!
//! Test modules load one hardcoded local `*.test.yml` override through
//! `eos-config`; this module resolves the merged document into the concrete
//! Docker harness settings.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{bail, Context, Result};
use serde::Deserialize;

use crate::configs::isolated_workspace::IsolatedWorkspaceConfig;
use crate::ConfigDocument;

/// How nodes (containers) map to tests.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NodeMode {
    /// One container; tests isolate via a fresh `layer_stack_root`.
    Shared,
    /// Up to `sandboxes` containers behind a semaphore; lease + fresh root.
    Pool,
    /// One container per test file (approximated as `Pool` here).
    PerFile,
    /// One container per test (max isolation; overlay/isolated tiers).
    PerTest,
}

impl NodeMode {
    const fn from_config(value: E2eNodeMode) -> Self {
        match value {
            E2eNodeMode::Shared => Self::Shared,
            E2eNodeMode::Pool => Self::Pool,
            E2eNodeMode::PerFile => Self::PerFile,
            E2eNodeMode::PerTest => Self::PerTest,
        }
    }
}

/// Fully resolved harness configuration.
#[derive(Debug, Clone)]
pub struct Config {
    /// Docker image reference.
    pub image: String,
    /// `--platform` value (e.g. `linux/amd64`), or `None` to let docker decide.
    pub platform: Option<String>,
    /// Absolute host path to the `eosd` binary uploaded into each container.
    pub eosd_path: PathBuf,
    /// Container directory that receives the daemon binary and log/socket files.
    pub remote_daemon_dir: PathBuf,
    /// Container path to the uploaded daemon binary.
    pub remote_eosd_path: PathBuf,
    /// Container root under which the pool mints per-test LayerStack state.
    pub root_dir: PathBuf,
    /// `--cap-add` capability names.
    pub cap_add: Vec<String>,
    /// `--security-opt` values.
    pub security_opt: Vec<String>,
    /// `--tmpfs` specs for writable e2e and overlay scratch mounts.
    pub tmpfs: Vec<String>,
    /// Daemon TCP port inside the container.
    pub tcp_port: u16,
    /// Node-pool cap (concurrent sandboxes).
    pub sandboxes: usize,
    /// Node mode.
    pub mode: NodeMode,
    /// Recycle a pooled node after this many checkouts.
    pub recycle_after: usize,
    /// Daemon readiness poll budget.
    pub ready_timeout: Duration,
    /// Per-request socket timeout.
    pub request_timeout: Duration,
    /// Base-build budget (slow on first warm).
    pub base_build_timeout: Duration,
    /// Canonical workload workspace root inside the sandbox container.
    pub workspace_root: String,
    /// Skip container teardown for inspection.
    pub keep_container: bool,
    /// A non-kept container self-removes after this long.
    pub non_kept_container_ttl: Duration,
    /// `limit` passed to `api.audit.pull`.
    pub audit_pull_limit: u64,
    /// Correctness, pressure, and performance workload knobs.
    pub workload: WorkloadConfig,
}

/// Fully resolved workload knobs used by live E2E tests.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkloadConfig {
    /// Concurrency ladder for tests that compare levels instead of one count.
    pub concurrency_levels: Vec<usize>,
    /// Bound repeated write, squash, refresh, or retry loops.
    pub write_iterations: usize,
    /// Number of samples before a perf/resource JSON artifact is emitted.
    pub sample_count: usize,
    /// Host-relative directory for JSON performance/resource artifacts.
    pub perf_artifact_dir: PathBuf,
    /// Workload operation budget independent of the socket timeout.
    pub timeout: Duration,
}

/// Typed `eos_e2e_test` section from `sandbox/config/prd.yml`.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EosE2eTestConfig {
    pub docker: E2eDockerConfig,
    pub pool: E2ePoolConfig,
    pub timeouts: E2eTimeoutConfig,
    pub audit: E2eAuditConfig,
    pub workload: E2eWorkloadConfig,
}

/// Docker/container defaults for the E2E harness.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct E2eDockerConfig {
    pub image: String,
    pub platform: Option<String>,
    pub eosd_path: PathBuf,
    pub remote_daemon_dir: PathBuf,
    pub remote_eosd_path: PathBuf,
    pub root_dir: PathBuf,
    pub cap_add: Vec<String>,
    pub security_opt: Vec<String>,
    pub tmpfs: Vec<String>,
    pub tcp_port: u16,
    pub non_kept_container_ttl_s: u64,
}

/// Node-pool defaults for the E2E harness.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct E2ePoolConfig {
    pub mode: E2eNodeMode,
    pub sandboxes: usize,
    pub recycle_after: usize,
    pub keep_container: bool,
}

/// How E2E nodes map to tests in the future YAML-backed loader.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum E2eNodeMode {
    Shared,
    Pool,
    PerFile,
    PerTest,
}

/// E2E harness timeout defaults.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct E2eTimeoutConfig {
    pub ready_s: u64,
    pub request_s: u64,
    pub base_build_s: u64,
}

/// E2E audit query defaults.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct E2eAuditConfig {
    pub pull_limit: u64,
}

/// Workload defaults for correctness, pressure, and performance E2E tests.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct E2eWorkloadConfig {
    pub concurrency_levels: Vec<usize>,
    pub write_iterations: usize,
    pub sample_count: usize,
    pub perf_artifact_dir: PathBuf,
    pub timeout_s: u64,
}

impl EosE2eTestConfig {
    /// Deserialize the `eos_e2e_test` section from a generic config document.
    ///
    /// # Errors
    /// Returns an error if the section is missing, malformed, or semantically
    /// invalid.
    pub fn from_document(doc: &ConfigDocument) -> Result<Self> {
        let config = doc
            .section::<Self>("eos_e2e_test")
            .context("deserialize eos_e2e_test config section")?;
        config.validate()?;
        Ok(config)
    }

    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when a field violates E2E harness policy.
    pub fn validate(&self) -> Result<()> {
        require_non_empty(&self.docker.image, "eos_e2e_test.docker.image")?;
        if let Some(platform) = &self.docker.platform {
            require_non_empty(platform, "eos_e2e_test.docker.platform")?;
        }
        require_non_empty_path(&self.docker.eosd_path, "eos_e2e_test.docker.eosd_path")?;
        require_absolute(
            &self.docker.remote_daemon_dir,
            "eos_e2e_test.docker.remote_daemon_dir",
        )?;
        require_absolute(
            &self.docker.remote_eosd_path,
            "eos_e2e_test.docker.remote_eosd_path",
        )?;
        require_absolute(&self.docker.root_dir, "eos_e2e_test.docker.root_dir")?;
        require_non_empty_items(&self.docker.cap_add, "eos_e2e_test.docker.cap_add")?;
        require_non_empty_items(
            &self.docker.security_opt,
            "eos_e2e_test.docker.security_opt",
        )?;
        require_non_empty_items(&self.docker.tmpfs, "eos_e2e_test.docker.tmpfs")?;
        require_u16_nonzero(self.docker.tcp_port, "eos_e2e_test.docker.tcp_port")?;
        require_u64_at_least(
            self.docker.non_kept_container_ttl_s,
            1,
            "eos_e2e_test.docker.non_kept_container_ttl_s",
        )?;
        require_usize_at_least(self.pool.sandboxes, 1, "eos_e2e_test.pool.sandboxes")?;
        require_usize_at_least(
            self.pool.recycle_after,
            1,
            "eos_e2e_test.pool.recycle_after",
        )?;
        require_u64_at_least(self.timeouts.ready_s, 1, "eos_e2e_test.timeouts.ready_s")?;
        require_u64_at_least(
            self.timeouts.request_s,
            1,
            "eos_e2e_test.timeouts.request_s",
        )?;
        require_u64_at_least(
            self.timeouts.base_build_s,
            1,
            "eos_e2e_test.timeouts.base_build_s",
        )?;
        require_u64_at_least(self.audit.pull_limit, 1, "eos_e2e_test.audit.pull_limit")?;
        require_concurrency_levels(
            &self.workload.concurrency_levels,
            "eos_e2e_test.workload.concurrency_levels",
        )?;
        require_usize_at_least(
            self.workload.write_iterations,
            1,
            "eos_e2e_test.workload.write_iterations",
        )?;
        require_usize_at_least(
            self.workload.sample_count,
            1,
            "eos_e2e_test.workload.sample_count",
        )?;
        require_non_empty_path(
            &self.workload.perf_artifact_dir,
            "eos_e2e_test.workload.perf_artifact_dir",
        )?;
        require_u64_at_least(
            self.workload.timeout_s,
            1,
            "eos_e2e_test.workload.timeout_s",
        )?;
        Ok(())
    }
}

impl Config {
    /// Load and fully resolve one test-local `*.test.yml` override.
    ///
    /// # Errors
    /// Returns an error if either config document cannot be read or validated.
    pub fn load_test_override(path: impl AsRef<Path>) -> Result<(Self, ConfigDocument)> {
        let doc = crate::load_test_override(path).context("load E2E test override")?;
        let config = Self::from_document(&doc)?;
        Ok((config, doc))
    }

    /// Resolve the harness configuration from an already-loaded document.
    ///
    /// # Errors
    /// Returns an error if required sections are missing or invalid.
    pub fn from_document(doc: &ConfigDocument) -> Result<Self> {
        let e2e = EosE2eTestConfig::from_document(doc)?;
        let isolated = doc
            .section::<IsolatedWorkspaceConfig>("isolated_workspace")
            .context("deserialize isolated_workspace config section")?;
        isolated
            .validate()
            .context("validate isolated_workspace config")?;
        Self::from_sections(e2e, isolated)
    }

    fn from_sections(e2e: EosE2eTestConfig, isolated: IsolatedWorkspaceConfig) -> Result<Self> {
        Ok(Self {
            image: e2e.docker.image,
            platform: e2e.docker.platform,
            eosd_path: resolve_eosd_path(&e2e.docker.eosd_path),
            remote_daemon_dir: e2e.docker.remote_daemon_dir,
            remote_eosd_path: e2e.docker.remote_eosd_path,
            root_dir: e2e.docker.root_dir,
            cap_add: e2e.docker.cap_add,
            security_opt: e2e.docker.security_opt,
            tmpfs: e2e.docker.tmpfs,
            tcp_port: e2e.docker.tcp_port,
            sandboxes: e2e.pool.sandboxes,
            mode: NodeMode::from_config(e2e.pool.mode),
            recycle_after: e2e.pool.recycle_after,
            ready_timeout: Duration::from_secs(e2e.timeouts.ready_s),
            request_timeout: Duration::from_secs(e2e.timeouts.request_s),
            base_build_timeout: Duration::from_secs(e2e.timeouts.base_build_s),
            workspace_root: isolated.workspace_root.to_string_lossy().into_owned(),
            keep_container: e2e.pool.keep_container,
            non_kept_container_ttl: Duration::from_secs(e2e.docker.non_kept_container_ttl_s),
            audit_pull_limit: e2e.audit.pull_limit,
            workload: WorkloadConfig {
                concurrency_levels: e2e.workload.concurrency_levels,
                write_iterations: e2e.workload.write_iterations,
                sample_count: e2e.workload.sample_count,
                perf_artifact_dir: e2e.workload.perf_artifact_dir,
                timeout: Duration::from_secs(e2e.workload.timeout_s),
            },
        })
    }
}

/// Resolve the `eosd` path: absolute as-is, relative against the sandbox
/// workspace root (`CARGO_MANIFEST_DIR/../..`).
fn resolve_eosd_path(value: &Path) -> PathBuf {
    let candidate = value.to_path_buf();
    if candidate.is_absolute() {
        return candidate;
    }
    let workspace_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    workspace_root.join(candidate)
}

fn require_non_empty(value: &str, field: &str) -> Result<()> {
    if value.trim().is_empty() {
        bail!("{field}: must be non-empty");
    }
    Ok(())
}

fn require_non_empty_path(path: &std::path::Path, field: &str) -> Result<()> {
    if path.as_os_str().is_empty() {
        bail!("{field}: must be non-empty");
    }
    Ok(())
}

fn require_absolute(path: &std::path::Path, field: &str) -> Result<()> {
    if !path.is_absolute() {
        bail!("{field}: must be an absolute path");
    }
    Ok(())
}

fn require_non_empty_items(values: &[String], field: &str) -> Result<()> {
    if values.iter().any(|value| value.trim().is_empty()) {
        bail!("{field}: must not contain empty strings");
    }
    Ok(())
}

fn require_concurrency_levels(values: &[usize], field: &str) -> Result<()> {
    if values.is_empty() {
        bail!("{field}: must contain at least one level");
    }
    let mut seen = BTreeSet::new();
    let mut previous = 0;
    for &value in values {
        if value == 0 {
            bail!("{field}: must not contain zero");
        }
        if !seen.insert(value) {
            bail!("{field}: duplicate level {value}");
        }
        if value < previous {
            bail!("{field}: must be sorted ascending");
        }
        previous = value;
    }
    Ok(())
}

fn require_u16_nonzero(value: u16, field: &str) -> Result<()> {
    if value == 0 {
        bail!("{field}: must be non-zero");
    }
    Ok(())
}

fn require_u64_at_least(value: u64, minimum: u64, field: &str) -> Result<()> {
    if value < minimum {
        bail!("{field}: must be at least {minimum}");
    }
    Ok(())
}

fn require_usize_at_least(value: usize, minimum: usize, field: &str) -> Result<()> {
    if value < minimum {
        bail!("{field}: must be at least {minimum}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prd_e2e_section_deserializes_and_validates() {
        let doc = crate::load_prd().expect("prd config loads");
        EosE2eTestConfig::from_document(&doc).expect("prd eos_e2e_test config is valid");
    }

    #[test]
    fn validation_rejects_invalid_e2e_values() {
        let mut cfg = prd_config();
        cfg.docker.image.clear();
        assert_invalid(cfg, "eos_e2e_test.docker.image");

        let mut cfg = prd_config();
        cfg.docker.remote_eosd_path = PathBuf::from("relative");
        assert_invalid(cfg, "eos_e2e_test.docker.remote_eosd_path");

        let mut cfg = prd_config();
        cfg.docker.tcp_port = 0;
        assert_invalid(cfg, "eos_e2e_test.docker.tcp_port");

        let mut cfg = prd_config();
        cfg.pool.sandboxes = 0;
        assert_invalid(cfg, "eos_e2e_test.pool.sandboxes");

        let mut cfg = prd_config();
        cfg.timeouts.ready_s = 0;
        assert_invalid(cfg, "eos_e2e_test.timeouts.ready_s");

        let mut cfg = prd_config();
        cfg.workload.concurrency_levels = vec![1, 0, 3];
        assert_invalid(cfg, "eos_e2e_test.workload.concurrency_levels");

        let mut cfg = prd_config();
        cfg.workload.concurrency_levels = vec![1, 3, 3];
        assert_invalid(cfg, "duplicate level 3");

        let mut cfg = prd_config();
        cfg.workload.write_iterations = 0;
        assert_invalid(cfg, "eos_e2e_test.workload.write_iterations");
    }

    fn prd_config() -> EosE2eTestConfig {
        let doc = crate::load_prd().expect("prd config loads");
        EosE2eTestConfig::from_document(&doc).expect("eos_e2e_test section deserializes")
    }

    fn assert_invalid(config: EosE2eTestConfig, field: &str) {
        let err = config.validate().expect_err("config should be invalid");
        let message = err.to_string();
        assert!(message.contains(field), "{message}");
    }
}
