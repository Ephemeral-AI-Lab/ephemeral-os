//! Topology/runtime config for the Rust E2E harness.
//!
//! Test modules load one hardcoded local `*.test.yml` override through
//! `config`; this module resolves the merged document into the concrete
//! Docker harness settings.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{bail, Context, Result};
use serde::Deserialize;

use crate::configs::isolated_workspace::IsolatedWorkspaceConfig;
use crate::{ConfigDocument, ConfigPath};

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
    /// Whether container bootstrap passes `--privileged`.
    pub privileged: bool,
    /// Absolute host path to the `eosd` binary uploaded into each container.
    pub eosd_path: PathBuf,
    /// Container directory that receives the daemon binary and log/socket files.
    pub remote_daemon_dir: PathBuf,
    /// Container path to the uploaded daemon binary.
    pub remote_eosd_path: PathBuf,
    /// Container path to the uploaded daemon configuration document.
    pub remote_config_path: PathBuf,
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
    /// Canonical workload workspace root inside the sandbox container.
    pub workspace_root: String,
    /// Skip container teardown for inspection.
    pub keep_container: bool,
    /// A non-kept container self-removes after this long.
    pub non_kept_container_ttl: Duration,
    /// Correctness, pressure, and performance workload knobs.
    pub workload: WorkloadConfig,
    /// Host-side report and trace artifact settings.
    pub artifacts: ArtifactConfig,
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

/// Fully resolved host-side report artifact knobs used by live E2E tests.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArtifactConfig {
    /// Exact report run root. Defaults to `test-reports/runs/<run-id>`.
    pub root_dir: Option<PathBuf>,
    /// Optional directory for JSON performance/resource artifacts.
    pub perf_dir: Option<PathBuf>,
    /// Optional directory for decoded full trace records.
    pub trace_dir: Option<PathBuf>,
    /// Optional directory for compact event JSONL rows.
    pub event_dir: Option<PathBuf>,
    /// Optional directory for the host trace-store SQLite audit DB.
    pub audit_dir: Option<PathBuf>,
    /// Optional directory for copied per-container daemon logs.
    pub daemon_log_dir: Option<PathBuf>,
    /// Trace/audit/event capture mode.
    pub dump_mode: ArtifactDumpMode,
}

/// Host-side trace/audit/event capture mode.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ArtifactDumpMode {
    Off,
    Failure,
    #[default]
    Always,
}

/// Typed `e2e_test` section from `eos-sandbox/config/prd.yml`.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EosE2eTestConfig {
    pub docker: E2eDockerConfig,
    pub pool: E2ePoolConfig,
    pub timeouts: E2eTimeoutConfig,
    pub workload: E2eWorkloadConfig,
    #[serde(default)]
    pub artifacts: E2eArtifactsConfig,
}

/// Docker/container defaults for the E2E harness.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct E2eDockerConfig {
    pub image: String,
    pub platform: Option<String>,
    #[serde(default = "default_docker_privileged")]
    pub privileged: bool,
    pub eosd_path: PathBuf,
    pub remote_daemon_dir: PathBuf,
    pub remote_eosd_path: PathBuf,
    #[serde(default = "default_remote_config_path")]
    pub remote_config_path: PathBuf,
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

/// Host-side artifact defaults for E2E reports and trace discovery.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct E2eArtifactsConfig {
    pub root_dir: Option<PathBuf>,
    pub perf_dir: Option<PathBuf>,
    pub trace_dir: Option<PathBuf>,
    pub event_dir: Option<PathBuf>,
    pub audit_dir: Option<PathBuf>,
    pub daemon_log_dir: Option<PathBuf>,
    pub dump_mode: ArtifactDumpMode,
}

impl EosE2eTestConfig {
    /// Deserialize the `e2e_test` section from a generic config document.
    ///
    /// # Errors
    /// Returns an error if the section is missing, malformed, or semantically
    /// invalid.
    pub fn from_document(doc: &ConfigDocument) -> Result<Self> {
        let config = doc
            .section::<Self>("e2e_test")
            .context("deserialize e2e_test config section")?;
        config.validate()?;
        Ok(config)
    }

    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when a field violates E2E harness policy.
    pub fn validate(&self) -> Result<()> {
        require_non_empty(&self.docker.image, "e2e_test.docker.image")?;
        if let Some(platform) = &self.docker.platform {
            require_non_empty(platform, "e2e_test.docker.platform")?;
        }
        require_non_empty_path(&self.docker.eosd_path, "e2e_test.docker.eosd_path")?;
        require_absolute(
            &self.docker.remote_daemon_dir,
            "e2e_test.docker.remote_daemon_dir",
        )?;
        require_absolute(
            &self.docker.remote_eosd_path,
            "e2e_test.docker.remote_eosd_path",
        )?;
        require_absolute(
            &self.docker.remote_config_path,
            "e2e_test.docker.remote_config_path",
        )?;
        require_absolute(&self.docker.root_dir, "e2e_test.docker.root_dir")?;
        require_non_empty_items(&self.docker.cap_add, "e2e_test.docker.cap_add")?;
        require_non_empty_items(&self.docker.security_opt, "e2e_test.docker.security_opt")?;
        require_non_empty_items(&self.docker.tmpfs, "e2e_test.docker.tmpfs")?;
        require_u16_nonzero(self.docker.tcp_port, "e2e_test.docker.tcp_port")?;
        require_u64_at_least(
            self.docker.non_kept_container_ttl_s,
            1,
            "e2e_test.docker.non_kept_container_ttl_s",
        )?;
        require_usize_at_least(self.pool.sandboxes, 1, "e2e_test.pool.sandboxes")?;
        require_usize_at_least(self.pool.recycle_after, 1, "e2e_test.pool.recycle_after")?;
        require_u64_at_least(self.timeouts.ready_s, 1, "e2e_test.timeouts.ready_s")?;
        require_u64_at_least(self.timeouts.request_s, 1, "e2e_test.timeouts.request_s")?;
        require_concurrency_levels(
            &self.workload.concurrency_levels,
            "e2e_test.workload.concurrency_levels",
        )?;
        require_usize_at_least(
            self.workload.write_iterations,
            1,
            "e2e_test.workload.write_iterations",
        )?;
        require_usize_at_least(
            self.workload.sample_count,
            1,
            "e2e_test.workload.sample_count",
        )?;
        require_non_empty_path(
            &self.workload.perf_artifact_dir,
            "e2e_test.workload.perf_artifact_dir",
        )?;
        require_u64_at_least(self.workload.timeout_s, 1, "e2e_test.workload.timeout_s")?;
        require_optional_path(&self.artifacts.root_dir, "e2e_test.artifacts.root_dir")?;
        require_optional_path(&self.artifacts.perf_dir, "e2e_test.artifacts.perf_dir")?;
        require_optional_path(&self.artifacts.trace_dir, "e2e_test.artifacts.trace_dir")?;
        require_optional_path(&self.artifacts.event_dir, "e2e_test.artifacts.event_dir")?;
        require_optional_path(&self.artifacts.audit_dir, "e2e_test.artifacts.audit_dir")?;
        require_optional_path(
            &self.artifacts.daemon_log_dir,
            "e2e_test.artifacts.daemon_log_dir",
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
            privileged: e2e.docker.privileged,
            eosd_path: resolve_eosd_path(&e2e.docker.eosd_path),
            remote_daemon_dir: e2e.docker.remote_daemon_dir,
            remote_eosd_path: e2e.docker.remote_eosd_path,
            remote_config_path: e2e.docker.remote_config_path,
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
            workspace_root: isolated.workspace_root.to_string_lossy().into_owned(),
            keep_container: e2e.pool.keep_container,
            non_kept_container_ttl: Duration::from_secs(e2e.docker.non_kept_container_ttl_s),
            workload: WorkloadConfig {
                concurrency_levels: e2e.workload.concurrency_levels,
                write_iterations: e2e.workload.write_iterations,
                sample_count: e2e.workload.sample_count,
                perf_artifact_dir: e2e.workload.perf_artifact_dir,
                timeout: Duration::from_secs(e2e.workload.timeout_s),
            },
            artifacts: ArtifactConfig {
                root_dir: e2e.artifacts.root_dir,
                perf_dir: e2e.artifacts.perf_dir,
                trace_dir: e2e.artifacts.trace_dir,
                event_dir: e2e.artifacts.event_dir,
                audit_dir: e2e.artifacts.audit_dir,
                daemon_log_dir: e2e.artifacts.daemon_log_dir,
                dump_mode: e2e.artifacts.dump_mode,
            },
        })
    }
}

fn default_remote_config_path() -> PathBuf {
    ConfigPath::prd()
        .map(|path| path.as_path().to_path_buf())
        .unwrap_or_else(|_| PathBuf::from("/eos/runtime/daemon/config.yml"))
}

const fn default_docker_privileged() -> bool {
    true
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

fn require_optional_path(path: &Option<PathBuf>, field: &str) -> Result<()> {
    if let Some(path) = path {
        require_non_empty_path(path, field)?;
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
#[path = "../../tests/unit/configs/e2e_test.rs"]
mod tests;
