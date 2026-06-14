//! Daemon startup config-path wiring checks.
//!
//! This test binary uses its own E2E config override so the global live-pool
//! cache can prove a non-default remote daemon config path independently of the
//! main daemon suite.

use anyhow::Result;
use e2e_test::live_pool_with_config;
use protocol::catalog;
use serde_json::json;

const E2E_CONFIG: &str =
    "crates/e2e-test/tests/daemon-config/config/non-default-remote-config.test.yml";
const REMOTE_CONFIG_PATH: &str = "/eos/runtime/daemon/non-default-config.yml";

#[test]
fn daemon_starts_and_restarts_from_non_default_remote_config_path() -> Result<()> {
    let Some(pool) = live_pool_with_config(E2E_CONFIG)? else {
        eprintln!("skipping live e2e-test; enable with `--features e2e`");
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease
        .container()
        .exec(&["test", "-s", REMOTE_CONFIG_PATH])?;

    let ready = lease.call(catalog::SANDBOX_RUNTIME_READY, json!({}))?;
    assert_eq!(
        ready["status"], "ok",
        "daemon reads non-default config path on startup: {ready}"
    );

    lease.restart_daemon()?;
    let restarted = lease.call(catalog::SANDBOX_RUNTIME_READY, json!({}))?;
    assert_eq!(
        restarted["status"], "ok",
        "daemon reads non-default config path on restart: {restarted}"
    );
    Ok(())
}
