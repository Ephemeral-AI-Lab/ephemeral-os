use anyhow::Result;
use eos_protocol::ops;
use serde_json::json;

use crate::support::{
    as_bool, as_i64, as_str, live_pool_or_skip, wait_for_active_leases, wait_for_session_count,
};

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
fn cancel_burst_returns_sessions_and_leases_to_zero() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // Five long-running sessions occupy BOTH leak surfaces at once: the command
    // session manager (PTY children) and the LayerStack lease registry.
    let ids: Vec<String> = (0..5)
        .map(|index| start_sleep(&lease, &format!("burst-{index}")))
        .collect::<Result<_>>()?;
    let count = lease.call_ok(ops::API_V1_COMMAND_SESSION_COUNT, json!({}))?;
    assert_eq!(as_i64(&count, "count")?, 5, "five live sessions: {count}");
    let leased = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
    assert!(
        as_i64(&leased, "active_leases")? >= 5,
        "five running commands must each hold a lease: {leased}"
    );

    for id in ids {
        lease.call_ok(
            ops::API_V1_COMMAND_CANCEL,
            json!({"command_session_id": id}),
        )?;
    }
    // Both surfaces must drain together: a leak in either (stranded PTY child OR
    // stranded layer lease) leaves a nonzero count.
    wait_for_session_count(&lease, 0)?;
    let released = wait_for_active_leases(&lease, 0)?;
    assert_eq!(as_i64(&released, "active_leases")?, 0, "{released}");
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
