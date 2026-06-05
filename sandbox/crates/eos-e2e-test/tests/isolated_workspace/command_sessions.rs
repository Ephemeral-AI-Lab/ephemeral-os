use anyhow::Result;
use eos_protocol::ops;
use serde_json::json;

use crate::common::{as_str, live_pool_or_skip};

#[test]
fn iws_same_port_discard() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    let first = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "python3 -m http.server 39001 >/eos/scratch/e2e/eos-e2e-http.log 2>&1",
            "yield_time_ms": 100,
            "timeout_seconds": 120,
            "max_output_tokens": 500
        }),
    )?;
    assert_eq!(as_str(&first, "status")?, "running");
    let first_id = as_str(&first, "command_session_id")?.to_owned();
    lease.call_ok(
        ops::API_V1_COMMAND_CANCEL,
        json!({"command_session_id": first_id}),
    )?;
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({"grace_s": 0.1}))?;

    lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    let second = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "python3 -m http.server 39001 >/eos/scratch/e2e/eos-e2e-http.log 2>&1",
            "yield_time_ms": 100,
            "timeout_seconds": 120,
            "max_output_tokens": 500
        }),
    )?;
    assert_eq!(
        as_str(&second, "status")?,
        "running",
        "same isolated port should be reusable after exit discard: {second}"
    );
    if let Some(id) = second
        .get("command_session_id")
        .and_then(serde_json::Value::as_str)
    {
        lease.call_ok(
            ops::API_V1_COMMAND_CANCEL,
            json!({"command_session_id": id}),
        )?;
    }
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({"grace_s": 0.1}))?;
    Ok(())
}
