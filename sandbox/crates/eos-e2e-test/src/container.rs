//! E2E adapter over [`eos_sandbox_host::container`]: maps the harness config
//! onto container/daemon specs, owns the e2e label vocabulary, and implements
//! warm-pool adoption keyed by a config+binary digest.

use anyhow::{Context, Result};
use eos_config::ConfigPath;
use sha2::{Digest, Sha256};

pub use eos_sandbox_host::container::DaemonContainer;
use eos_sandbox_host::container::{ContainerLifetime, ContainerSpec, DaemonSpec};
pub use eos_sandbox_host::docker::docker_available;
use eos_sandbox_host::docker::{container_label, remove_labeled_containers, running_container_ids};

use crate::config::{Config, NodeMode};
use crate::unique_suffix;

pub(crate) const POOL_LABEL: &str = "eos.e2e.pool";
const AUTH_LABEL: &str = "eos.e2e.auth";
const CONFIG_DIGEST_LABEL: &str = "eos.e2e.config_sha256";

/// Start a fresh e2e daemon container from the harness config.
///
/// # Errors
/// Returns an error if any docker step fails or the daemon never becomes ready.
pub(crate) fn start_node(config: &Config, config_yaml: &str) -> Result<DaemonContainer> {
    let name = format!("eos-e2e-{}", unique_suffix());
    let token = format!("tok-{}", unique_suffix());
    let config_digest = runtime_digest(config, config_yaml)?;
    let keep = config.keep_container && config.mode != NodeMode::PerTest;
    let container = ContainerSpec {
        name,
        image: config.image.clone(),
        platform: config.platform.clone(),
        cap_add: config.cap_add.clone(),
        security_opt: config.security_opt.clone(),
        tmpfs: config.tmpfs.clone(),
        labels: vec![
            (POOL_LABEL.to_owned(), config.image.clone()),
            (AUTH_LABEL.to_owned(), token.clone()),
            (CONFIG_DIGEST_LABEL.to_owned(), config_digest),
        ],
        lifetime: if keep {
            ContainerLifetime::Keep
        } else {
            ContainerLifetime::SelfDestruct {
                ttl: config.non_kept_container_ttl,
            }
        },
    };
    DaemonContainer::start(&container, &daemon_spec(config, config_yaml)?, token)
}

/// Adopt already-running warm e2e containers for this image.
///
/// Containers are accepted only when their auth label is present, their config
/// digest matches, their published daemon port resolves, and the daemon passes
/// the ready gate.
pub(crate) fn adopt_healthy(config: &Config, config_yaml: &str) -> Vec<DaemonContainer> {
    let Ok(digest) = runtime_digest(config, config_yaml) else {
        return Vec::new();
    };
    let Ok(daemon) = daemon_spec(config, config_yaml) else {
        return Vec::new();
    };
    running_container_ids(&[
        format!("{POOL_LABEL}={}", config.image),
        format!("{CONFIG_DIGEST_LABEL}={digest}"),
    ])
    .iter()
    .filter_map(|id| {
        let token = container_label(id, AUTH_LABEL).ok()?;
        DaemonContainer::adopt(id, token, &daemon).ok()
    })
    .collect()
}

/// The daemon bring-up spec shared by start, adopt, and restart.
pub(crate) fn daemon_spec(config: &Config, config_yaml: &str) -> Result<DaemonSpec> {
    let remote_config_path = ConfigPath::prd()
        .context("resolve compiled daemon config path")?
        .as_path()
        .to_path_buf();
    Ok(DaemonSpec {
        eosd_path: config.eosd_path.clone(),
        remote_daemon_dir: config.remote_daemon_dir.clone(),
        remote_eosd_path: config.remote_eosd_path.clone(),
        remote_config_path,
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

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;
    use std::time::Duration;

    use anyhow::Result;

    use crate::config::{Config, NodeMode, WorkloadConfig};

    use super::runtime_digest;

    fn digest_test_config(eosd_path: PathBuf) -> Config {
        Config {
            image: "image".to_owned(),
            platform: None,
            eosd_path,
            remote_daemon_dir: PathBuf::from("/eos/runtime/daemon"),
            remote_eosd_path: PathBuf::from("/eos/runtime/daemon/eosd"),
            root_dir: PathBuf::from("/eos/state/e2e"),
            cap_add: Vec::new(),
            security_opt: Vec::new(),
            tmpfs: Vec::new(),
            tcp_port: 37_657,
            sandboxes: 1,
            mode: NodeMode::Pool,
            recycle_after: 50,
            ready_timeout: Duration::from_secs(1),
            request_timeout: Duration::from_secs(1),
            base_build_timeout: Duration::from_secs(1),
            workspace_root: "/testbed".to_owned(),
            keep_container: true,
            non_kept_container_ttl: Duration::from_secs(60),
            audit_pull_limit: 100,
            workload: WorkloadConfig {
                concurrency_levels: vec![1, 3, 6, 12],
                write_iterations: 1,
                sample_count: 1,
                perf_artifact_dir: PathBuf::from("target/e2e-perf"),
                timeout: Duration::from_secs(1),
            },
        }
    }

    #[test]
    fn runtime_digest_tracks_config_and_eosd_bytes() -> Result<()> {
        let root =
            std::env::temp_dir().join(format!("eos-e2e-runtime-digest-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root)?;
        let eosd_path = root.join("eosd");
        fs::write(&eosd_path, b"daemon-v1")?;
        let config = digest_test_config(eosd_path);
        let baseline = runtime_digest(
            &config,
            "daemon:\n  layer_stack:\n    auto_squash_max_depth: 100\n",
        )?;
        let override_digest = runtime_digest(
            &config,
            "daemon:\n  layer_stack:\n    auto_squash_max_depth: 8\n",
        )?;

        assert_eq!(
            baseline,
            runtime_digest(
                &config,
                "daemon:\n  layer_stack:\n    auto_squash_max_depth: 100\n",
            )?
        );
        assert_eq!(baseline.len(), 64);
        assert_ne!(baseline, override_digest);
        fs::write(&config.eosd_path, b"daemon-v2")?;
        let rebuilt_digest = runtime_digest(
            &config,
            "daemon:\n  layer_stack:\n    auto_squash_max_depth: 100\n",
        )?;
        assert_ne!(baseline, rebuilt_digest);

        let _ = fs::remove_dir_all(root);
        Ok(())
    }
}
