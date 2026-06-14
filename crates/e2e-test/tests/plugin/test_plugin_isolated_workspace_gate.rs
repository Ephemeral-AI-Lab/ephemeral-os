use anyhow::Result;
use protocol::{catalog, OperationStatus};
use serde_json::json;

use crate::support::{live_pool_or_skip, operation_envelope};

#[test]
fn generic_plugin_rejected_in_isolated_workspace() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let response = lease.call("plugin.generic.query", json!({"path": "anything.txt"}))?;
    let envelope = operation_envelope(&response)?;
    assert_eq!(envelope.status(), OperationStatus::Error, "{response}");
    let fault = envelope.fault().expect("error envelope carries fault");
    assert_eq!(fault.kind, "forbidden_in_isolated_workspace", "{response}");
    lease.call_ok(catalog::SANDBOX_ISOLATION_EXIT, json!({}))?;
    Ok(())
}
