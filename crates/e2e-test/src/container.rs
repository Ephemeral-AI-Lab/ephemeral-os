//! E2E adapter over [`host::e2e_support`]: maps the harness config
//! onto container/daemon specs, owns the e2e label vocabulary, and implements
//! warm-pool adoption keyed by a config+binary digest.

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

use host::e2e_support::{
    container_ids_by_ancestor, container_label, copy_path_from_container,
    remove_containers_by_label_filters, remove_labeled_containers, running_container_ids,
    ContainerLifetime, ContainerSpec, DaemonSpec,
};
pub use host::e2e_support::{docker_available, DaemonContainer};

use crate::config::{Config, NodeMode};
use crate::run::RunContext;
use crate::unique_suffix;

pub(crate) const POOL_LABEL: &str = "eos.e2e.pool";
pub(crate) const RUN_ID_LABEL: &str = "eos.e2e.run_id";
const AUTH_LABEL: &str = "eos.e2e.auth";
const FORWARD_AUTH_LABEL: &str = "eos.e2e.forward_auth";
const CONFIG_DIGEST_LABEL: &str = "eos.e2e.config_sha256";
const DAEMON_LOG_PATH_LABEL: &str = "eos.e2e.daemon_log_path";
const DEFAULT_DAEMON_LOG_PATH: &str = "/eos/runtime/daemon/runtime.log";

/// Start a fresh e2e daemon container from the harness config.
///
/// # Errors
/// Returns an error if any docker step fails or the daemon never becomes ready.
pub(crate) fn start_node(
    config: &Config,
    config_yaml: &str,
    run: &RunContext,
) -> Result<DaemonContainer> {
    let name = format!("eos-e2e-{}", unique_suffix());
    let token = format!("tok-{}", unique_suffix());
    let forward_token = format!("fwd-{}", unique_suffix());
    let config_digest = runtime_digest(config, config_yaml)?;
    let keep = config.keep_container && config.mode != NodeMode::PerTest;
    let daemon_log_path = config
        .remote_daemon_dir
        .join("runtime.log")
        .to_string_lossy()
        .into_owned();
    let container = ContainerSpec {
        name,
        image: config.image.clone(),
        platform: config.platform.clone(),
        privileged: config.privileged,
        cap_add: config.cap_add.clone(),
        security_opt: config.security_opt.clone(),
        tmpfs: config.tmpfs.clone(),
        labels: vec![
            (POOL_LABEL.to_owned(), config.image.clone()),
            (RUN_ID_LABEL.to_owned(), run.run_id().to_owned()),
            (AUTH_LABEL.to_owned(), token.clone()),
            (FORWARD_AUTH_LABEL.to_owned(), forward_token.clone()),
            (CONFIG_DIGEST_LABEL.to_owned(), config_digest),
            (DAEMON_LOG_PATH_LABEL.to_owned(), daemon_log_path),
        ],
        lifetime: if keep {
            ContainerLifetime::Keep
        } else {
            ContainerLifetime::SelfDestruct {
                ttl: config.non_kept_container_ttl,
            }
        },
    };
    DaemonContainer::start_with_forward_token(
        &container,
        &daemon_spec(config, config_yaml)?,
        token,
        forward_token,
    )
}

/// Adopt already-running warm e2e containers for this image.
///
/// Containers are accepted only when their auth label is present, their config
/// digest matches, their published daemon port resolves, and the daemon passes
/// the ready gate.
pub(crate) fn adopt_healthy(
    config: &Config,
    config_yaml: &str,
    run: &RunContext,
) -> Vec<DaemonContainer> {
    let Ok(digest) = runtime_digest(config, config_yaml) else {
        return Vec::new();
    };
    let Ok(daemon) = daemon_spec(config, config_yaml) else {
        return Vec::new();
    };
    running_container_ids(&[
        format!("{POOL_LABEL}={}", config.image),
        format!("{RUN_ID_LABEL}={}", run.run_id()),
        format!("{CONFIG_DIGEST_LABEL}={digest}"),
    ])
    .iter()
    .filter_map(|id| {
        let token = container_label(id, AUTH_LABEL).ok()?;
        let forward_token = container_label(id, FORWARD_AUTH_LABEL).ok()?;
        DaemonContainer::adopt_with_forward_token(id, token, forward_token, &daemon).ok()
    })
    .collect()
}

/// The daemon bring-up spec shared by start, adopt, and restart.
pub(crate) fn daemon_spec(config: &Config, config_yaml: &str) -> Result<DaemonSpec> {
    Ok(DaemonSpec {
        eosd_path: config.eosd_path.clone(),
        remote_daemon_dir: config.remote_daemon_dir.clone(),
        remote_eosd_path: config.remote_eosd_path.clone(),
        remote_config_path: config.remote_config_path.clone(),
        config_yaml: config_yaml.to_owned(),
        extra_dirs: vec![config.root_dir.clone()],
        tcp_port: config.tcp_port,
        ready_timeout: config.ready_timeout,
        request_timeout: config.request_timeout,
    })
}

/// Identity digest for warm-container adoption: the merged config document
/// plus the exact `eosd` binary bytes.
fn runtime_digest(config: &Config, config_yaml: &str) -> Result<String> {
    let mut hasher = Sha256::new();
    hasher.update(config_yaml.as_bytes());
    hasher.update(b"\0remote_config_path\0");
    hasher.update(config.remote_config_path.to_string_lossy().as_bytes());
    hasher.update(b"\0eosd\0");
    let eosd = std::fs::read(&config.eosd_path).with_context(|| {
        format!(
            "read eosd binary for digest: {}",
            config.eosd_path.display()
        )
    })?;
    hasher.update(eosd);
    Ok(format!("{:x}", hasher.finalize()))
}

/// Remove all `eos-e2e-*` containers left by prior harness runs.
///
/// # Errors
/// Returns an error if Docker is reachable but listing or removing containers
/// fails.
pub fn reap_e2e_containers() -> Result<usize> {
    remove_labeled_containers(POOL_LABEL)
}

/// Remove only E2E containers owned by one run id.
///
/// # Errors
/// Returns an error if Docker is reachable but listing or removing containers
/// fails.
pub fn reap_e2e_containers_for_run(run_id: &str) -> Result<usize> {
    remove_containers_by_label_filters(&[POOL_LABEL.to_owned(), format!("{RUN_ID_LABEL}={run_id}")])
}

/// Fail before launch if too many configured-image containers already exist.
///
/// This intentionally counts all containers for the image, not just containers
/// labelled by this harness, so parallel runs do not hide Docker pressure.
///
/// # Errors
/// Returns an error if Docker cannot list image containers or the count exceeds
/// `cap`.
pub fn ensure_existing_image_container_cap(
    image: &str,
    cap: usize,
    run_id: &str,
    root_run_id: Option<&str>,
) -> Result<()> {
    let ids = container_ids_by_ancestor(image)
        .with_context(|| format!("count existing containers for image {image}"))?;
    let existing = ids
        .iter()
        .filter(|id| {
            container_label(id, RUN_ID_LABEL).map_or(true, |label| {
                !run_id_belongs_to_current_runner(&label, run_id, root_run_id)
            })
        })
        .count();
    if existing > cap {
        anyhow::bail!(
            "found {existing} pre-existing {image} container(s), over cap {cap}; remove stale containers before live E2E"
        );
    }
    Ok(())
}

fn run_id_belongs_to_current_runner(label: &str, run_id: &str, root_run_id: Option<&str>) -> bool {
    label == run_id
        || root_run_id.is_some_and(|root| label == root || label.starts_with(&format!("{root}-")))
}

/// Best-effort copy of daemon runtime logs for containers owned by one run id.
///
/// Missing logs are ignored because some suites use self-removing per-test
/// containers; the report should stay diagnostic-only and not change pass/fail
/// semantics.
///
/// # Errors
/// Returns an error only if the host destination cannot be created.
pub fn copy_daemon_logs_for_run(run_id: &str, dest_dir: &Path) -> Result<usize> {
    fs::create_dir_all(dest_dir)
        .with_context(|| format!("create daemon log directory {}", dest_dir.display()))?;
    let ids = running_container_ids(&[POOL_LABEL.to_owned(), format!("{RUN_ID_LABEL}={run_id}")]);
    let mut copied = 0;
    for id in ids {
        let remote_path = container_label(&id, DAEMON_LOG_PATH_LABEL)
            .unwrap_or_else(|_| DEFAULT_DAEMON_LOG_PATH.to_owned());
        let dest = daemon_log_dest(dest_dir, &id);
        if copy_path_from_container(&id, &remote_path, &dest).is_ok() {
            copied += 1;
        }
    }
    Ok(copied)
}

fn daemon_log_dest(dest_dir: &Path, container_id: &str) -> PathBuf {
    dest_dir
        .join(container_id.chars().take(12).collect::<String>())
        .join("runtime.log")
}

#[cfg(test)]
#[path = "../tests/unit/container.rs"]
mod tests;
