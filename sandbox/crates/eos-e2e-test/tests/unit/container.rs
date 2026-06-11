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
        workspace_root: "/testbed".to_owned(),
        keep_container: true,
        non_kept_container_ttl: Duration::from_secs(60),
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
    let root = std::env::temp_dir().join(format!("eos-e2e-runtime-digest-{}", std::process::id()));
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
