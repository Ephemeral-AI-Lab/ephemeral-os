//! Topology/environment config: the committed `e2e.toml` plus `EOS_E2E_*` env
//! overrides and `[profile.*]` selection.
//!
//! Precedence (highest first): `EOS_E2E_*` env var > selected `[profile.<name>]`
//! (via `EOS_E2E_PROFILE`) > the file's top-level tables > built-in defaults.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use eos_config::ConfigDocument;
use serde::Deserialize;

const DEFAULT_EOS_WORKSPACE_ROOT: &str = "/testbed";

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
    fn parse(value: &str) -> Result<Self> {
        match value {
            "shared" => Ok(Self::Shared),
            "pool" => Ok(Self::Pool),
            "per-file" => Ok(Self::PerFile),
            "per-test" => Ok(Self::PerTest),
            other => anyhow::bail!("unknown EOS_E2E_NODE_MODE {other:?}"),
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
    /// `limit` passed to `api.audit.pull`.
    pub audit_pull_limit: u64,
    /// `EOS_ISOLATED_WORKSPACE_UPPERDIR_BYTES` passed into the daemon container.
    pub isolated_upperdir_bytes: u64,
}

/// Typed `eos_e2e_test` section from `sandbox/config/prd.yml`.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EosE2eTestConfig {
    pub docker: E2eDockerConfig,
    pub pool: E2ePoolConfig,
    pub timeouts: E2eTimeoutConfig,
    pub audit: E2eAuditConfig,
    pub isolated_workspace_overrides: Option<E2eIsolatedWorkspaceOverrides>,
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

/// Harness-owned isolated-workspace runtime overrides for live Docker tests.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct E2eIsolatedWorkspaceOverrides {
    pub enabled: Option<bool>,
    pub upperdir_bytes: Option<u64>,
    pub memavail_fraction: Option<f64>,
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
        if let Some(overrides) = &self.isolated_workspace_overrides {
            if let Some(upperdir_bytes) = overrides.upperdir_bytes {
                require_u64_at_least(
                    upperdir_bytes,
                    1,
                    "eos_e2e_test.isolated_workspace_overrides.upperdir_bytes",
                )?;
            }
            if let Some(memavail_fraction) = overrides.memavail_fraction {
                require_ratio(
                    memavail_fraction,
                    "eos_e2e_test.isolated_workspace_overrides.memavail_fraction",
                )?;
            }
        }
        Ok(())
    }
}

#[derive(Debug, Default, Deserialize)]
struct FileConfig {
    docker: Option<DockerCfg>,
    concurrency: Option<ConcurrencyCfg>,
    timeouts: Option<TimeoutsCfg>,
    run: Option<RunCfg>,
    workspace: Option<WorkspaceCfg>,
    isolated: Option<IsolatedCfg>,
    profile: Option<BTreeMap<String, ProfileCfg>>,
}

#[derive(Debug, Default, Deserialize)]
struct DockerCfg {
    image: Option<String>,
    platform: Option<String>,
    eosd: Option<String>,
    cap_add: Option<Vec<String>>,
    security_opt: Option<Vec<String>>,
    tmpfs: Option<TmpfsCfg>,
    tcp_port: Option<u16>,
}

#[derive(Debug, Default, Deserialize)]
struct ConcurrencyCfg {
    sandboxes: Option<usize>,
    mode: Option<String>,
    recycle_after: Option<usize>,
}

#[derive(Debug, Default, Deserialize)]
struct TimeoutsCfg {
    ready: Option<u64>,
    request: Option<u64>,
    base_build: Option<u64>,
}

#[derive(Debug, Default, Deserialize)]
struct RunCfg {
    keep_container: Option<bool>,
    audit_pull_limit: Option<u64>,
}

#[derive(Debug, Default, Deserialize)]
struct WorkspaceCfg {
    root: Option<String>,
}

#[derive(Debug, Default, Deserialize)]
struct IsolatedCfg {
    upperdir_bytes: Option<u64>,
}

#[derive(Debug, Default, Deserialize)]
struct ProfileCfg {
    sandboxes: Option<usize>,
    mode: Option<String>,
    recycle_after: Option<usize>,
}

impl Config {
    /// Load and fully resolve the config, applying file, profile, and env layers.
    ///
    /// # Errors
    /// Returns an error if the config file is present but malformed, or if an
    /// env override has an invalid value.
    pub fn load() -> Result<Self> {
        let file = load_file_config()?;
        let docker = file.docker.unwrap_or_default();
        let concurrency = file.concurrency.unwrap_or_default();
        let timeouts = file.timeouts.unwrap_or_default();
        let run = file.run.unwrap_or_default();
        let workspace = file.workspace.unwrap_or_default();
        let isolated = file.isolated.unwrap_or_default();
        let profile = select_profile(file.profile.as_ref());

        // `EOS_LIVE_E2E_IMAGE` is the shared live-e2e convention (also read by the
        // Python provider harness); `EOS_E2E_IMAGE` is the crate-specific override.
        let image = env_str("EOS_E2E_IMAGE")
            .or_else(|| env_str("EOS_LIVE_E2E_IMAGE"))
            .or(docker.image)
            .unwrap_or_else(|| "sweevo-dask__dask-10042:latest".to_owned());
        let platform = env_str("EOS_E2E_PLATFORM").or(docker.platform);
        let eosd_rel = env_str("EOS_E2E_EOSD")
            .or(docker.eosd)
            .unwrap_or_else(|| "dist/eosd-linux-amd64".to_owned());

        let sandboxes = env_parse("EOS_E2E_SANDBOXES")?
            .or(profile.as_ref().and_then(|p| p.sandboxes))
            .or(concurrency.sandboxes)
            .unwrap_or(2)
            .max(1);
        let mode_str = env_str("EOS_E2E_NODE_MODE")
            .or(profile.as_ref().and_then(|p| p.mode.clone()))
            .or(concurrency.mode)
            .unwrap_or_else(|| "pool".to_owned());
        let recycle_after = profile
            .as_ref()
            .and_then(|p| p.recycle_after)
            .or(concurrency.recycle_after)
            .unwrap_or(50)
            .max(1);

        Ok(Self {
            image,
            platform,
            eosd_path: resolve_eosd_path(&eosd_rel),
            cap_add: docker
                .cap_add
                .unwrap_or_else(|| vec!["SYS_ADMIN".to_owned(), "NET_ADMIN".to_owned()]),
            security_opt: docker.security_opt.unwrap_or_else(|| {
                vec![
                    "seccomp=unconfined".to_owned(),
                    "apparmor=unconfined".to_owned(),
                ]
            }),
            tmpfs: docker
                .tmpfs
                .map(TmpfsCfg::into_vec)
                .unwrap_or_else(default_tmpfs),
            tcp_port: docker.tcp_port.unwrap_or(37657),
            sandboxes,
            mode: NodeMode::parse(&mode_str)?,
            recycle_after,
            ready_timeout: Duration::from_secs(timeouts.ready.unwrap_or(60)),
            request_timeout: Duration::from_secs(timeouts.request.unwrap_or(30)),
            base_build_timeout: Duration::from_secs(timeouts.base_build.unwrap_or(180)),
            workspace_root: env_str("EOS_E2E_WORKSPACE_ROOT")
                .or_else(|| env_str("EOS_WORKSPACE_ROOT"))
                .or(workspace.root)
                .unwrap_or_else(|| DEFAULT_EOS_WORKSPACE_ROOT.to_owned()),
            keep_container: env_bool("EOS_E2E_KEEP_CONTAINER")
                .or(run.keep_container)
                .unwrap_or(true),
            audit_pull_limit: run.audit_pull_limit.unwrap_or(2000),
            isolated_upperdir_bytes: env_parse_u64("EOS_E2E_ISOLATED_UPPERDIR_BYTES")?
                .or(isolated.upperdir_bytes)
                .unwrap_or(64 * 1024 * 1024),
        })
    }
}

fn load_file_config() -> Result<FileConfig> {
    let path = match std::env::var("EOS_E2E_CONFIG") {
        Ok(custom) => PathBuf::from(custom),
        Err(_) => PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("e2e.toml"),
    };
    match std::fs::read_to_string(&path) {
        Ok(text) => {
            toml::from_str(&text).with_context(|| format!("parse e2e config {}", path.display()))
        }
        // A missing file is fine; built-in defaults fully populate Config.
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(FileConfig::default()),
        Err(err) => Err(err).with_context(|| format!("read e2e config {}", path.display())),
    }
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum TmpfsCfg {
    One(String),
    Many(Vec<String>),
}

impl TmpfsCfg {
    fn into_vec(self) -> Vec<String> {
        match self {
            Self::One(value) => vec![value],
            Self::Many(values) => values,
        }
    }
}

fn default_tmpfs() -> Vec<String> {
    vec![
        "/eos/state:rw,exec,size=2g,mode=1777".to_owned(),
        "/eos/scratch:rw,exec,size=2g,mode=1777".to_owned(),
    ]
}

fn select_profile(profiles: Option<&BTreeMap<String, ProfileCfg>>) -> Option<ProfileCfg> {
    let name = std::env::var("EOS_E2E_PROFILE").ok()?;
    let table = profiles?;
    table.get(&name).map(|p| ProfileCfg {
        sandboxes: p.sandboxes,
        mode: p.mode.clone(),
        recycle_after: p.recycle_after,
    })
}

/// Resolve the `eosd` path: absolute as-is, relative against the sandbox
/// workspace root (`CARGO_MANIFEST_DIR/../..`).
fn resolve_eosd_path(value: &str) -> PathBuf {
    let candidate = PathBuf::from(value);
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

fn env_str(key: &str) -> Option<String> {
    std::env::var(key).ok().filter(|value| !value.is_empty())
}

fn env_parse(key: &str) -> Result<Option<usize>> {
    match env_str(key) {
        Some(value) => value
            .parse::<usize>()
            .map(Some)
            .with_context(|| format!("{key} must be a positive integer, got {value:?}")),
        None => Ok(None),
    }
}

fn env_parse_u64(key: &str) -> Result<Option<u64>> {
    match env_str(key) {
        Some(value) => value
            .parse::<u64>()
            .map(Some)
            .with_context(|| format!("{key} must be a positive integer, got {value:?}")),
        None => Ok(None),
    }
}

fn env_bool(key: &str) -> Option<bool> {
    env_str(key).map(|value| matches!(value.as_str(), "1" | "true" | "yes"))
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

fn require_ratio(value: f64, field: &str) -> Result<()> {
    if !(value.is_finite() && value > 0.0 && value <= 1.0) {
        bail!("{field}: must be greater than 0.0 and at most 1.0");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prd_e2e_section_deserializes_and_validates() {
        let doc = eos_config::load_prd().expect("prd config loads");
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
        cfg.isolated_workspace_overrides
            .as_mut()
            .expect("prd has isolated overrides")
            .memavail_fraction = Some(2.0);
        assert_invalid(
            cfg,
            "eos_e2e_test.isolated_workspace_overrides.memavail_fraction",
        );
    }

    fn prd_config() -> EosE2eTestConfig {
        let doc = eos_config::load_prd().expect("prd config loads");
        EosE2eTestConfig::from_document(&doc).expect("eos_e2e_test section deserializes")
    }

    fn assert_invalid(config: EosE2eTestConfig, field: &str) {
        let err = config.validate().expect_err("config should be invalid");
        let message = err.to_string();
        assert!(message.contains(field), "{message}");
    }
}
