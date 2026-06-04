//! Topology/environment config: the committed `e2e.toml` plus `EOS_E2E_*` env
//! overrides and `[profile.*]` selection.
//!
//! Precedence (highest first): `EOS_E2E_*` env var > selected `[profile.<name>]`
//! (via `EOS_E2E_PROFILE`) > the file's top-level tables > built-in defaults.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Context, Result};
use serde::Deserialize;

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
    /// `--tmpfs` spec for the overlay scratch mount.
    pub tmpfs: String,
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
    /// Skip container teardown for inspection.
    pub keep_container: bool,
    /// `limit` passed to `api.audit.pull`.
    pub audit_pull_limit: u64,
    /// `EOS_ISOLATED_WORKSPACE_UPPERDIR_BYTES` passed into the daemon container.
    pub isolated_upperdir_bytes: u64,
}

#[derive(Debug, Default, Deserialize)]
struct FileConfig {
    docker: Option<DockerCfg>,
    concurrency: Option<ConcurrencyCfg>,
    timeouts: Option<TimeoutsCfg>,
    run: Option<RunCfg>,
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
    tmpfs: Option<String>,
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
                .unwrap_or_else(|| "/eos:rw,exec,size=2g,mode=1777".to_owned()),
            tcp_port: docker.tcp_port.unwrap_or(37657),
            sandboxes,
            mode: NodeMode::parse(&mode_str)?,
            recycle_after,
            ready_timeout: Duration::from_secs(timeouts.ready.unwrap_or(60)),
            request_timeout: Duration::from_secs(timeouts.request.unwrap_or(30)),
            base_build_timeout: Duration::from_secs(timeouts.base_build.unwrap_or(180)),
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
