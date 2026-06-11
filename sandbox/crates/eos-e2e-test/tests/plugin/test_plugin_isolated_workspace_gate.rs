use anyhow::Result;
use eos_daemon::wire::ops;
use serde_json::json;

use crate::support::live_pool_or_skip;

#[test]
fn generic_plugin_rejected_in_isolated_workspace() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(ops::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let response = lease.call("plugin.generic.query", json!({"path": "anything.txt"}))?;
    assert_eq!(response["success"], false);
    assert_eq!(response["error"]["kind"], "forbidden_in_isolated_workspace");
    lease.call_ok(ops::SANDBOX_ISOLATION_EXIT, json!({}))?;
    Ok(())
}
