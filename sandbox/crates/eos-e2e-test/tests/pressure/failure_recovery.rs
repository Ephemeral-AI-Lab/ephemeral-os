use anyhow::Result;
use eos_protocol::ops;
use serde_json::json;

use crate::common::{as_bool, as_i64, as_str, live_pool_or_skip};

fn start_sleep(lease: &eos_e2e_test::NodeLease<'_>, marker: &str) -> Result<String> {
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!("sh -c 'echo {marker}; sleep 60'"),
            "yield_time_ms": 100,
            "timeout_seconds": 120,
            "max_output_tokens": 500
        }),
    )?;
    assert_eq!(as_str(&started, "status")?, "running");
    Ok(as_str(&started, "command_session_id")?.to_owned())
}

#[test]
fn daemon_recovers_after_midflight_cancel() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let id = start_sleep(&lease, "midflight")?;
    lease.call_ok(
        ops::API_V1_COMMAND_CANCEL,
        json!({"command_session_id": id}),
    )?;
    let ready = lease.call_ok(ops::API_RUNTIME_READY, json!({}))?;
    assert!(
        as_bool(&ready, "ready")?,
        "daemon should remain ready after midflight cancel: {ready}"
    );
    let count = lease.call_ok(ops::API_V1_COMMAND_SESSION_COUNT, json!({}))?;
    assert_eq!(
        as_i64(&count, "count")?,
        0,
        "cancel should not strand sessions: {count}"
    );
    Ok(())
}

#[test]
fn cancel_storm() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let ids: Vec<String> = (0..5)
        .map(|index| start_sleep(&lease, &format!("storm-{index}")))
        .collect::<Result<_>>()?;
    for id in ids {
        let cancel = lease.call_ok(
            ops::API_V1_COMMAND_CANCEL,
            json!({"command_session_id": id}),
        )?;
        assert!(
            matches!(as_str(&cancel, "status")?, "cancelled" | "ok" | "error"),
            "cancel storm should return structured status: {cancel}"
        );
    }
    let count = lease.call_ok(ops::API_V1_COMMAND_SESSION_COUNT, json!({}))?;
    assert_eq!(as_i64(&count, "count")?, 0);
    Ok(())
}
