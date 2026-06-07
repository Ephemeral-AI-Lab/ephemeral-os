//! Workspace-run cancel surface (§7): the per-caller and whole-sandbox cancel
//! ops tear down command sessions (cancel → discard, never publish), keyed by
//! `caller_id == agent_run_id`.

use anyhow::Result;
use eos_e2e_test::NodeLease;
use eos_protocol::ops;
use serde_json::json;

use crate::support::{
    array, as_i64, as_str, live_pool_or_skip, wait_for_active_leases, wait_for_session_count,
};

/// Start a `sleep 60` session for `caller_id` (or the lease default when `None`).
fn start_sleeping(lease: &NodeLease<'_>, caller_id: Option<&str>, marker: &str) -> Result<String> {
    let mut args = json!({
        "cmd": format!("sh -c 'echo {marker}; sleep 60'"),
        "yield_time_ms": 500,
        "timeout_seconds": 120,
    });
    if let Some(caller_id) = caller_id {
        args["caller_id"] = json!(caller_id);
    }
    let started = lease.call_ok(ops::API_V1_EXEC_COMMAND, args)?;
    assert_eq!(as_str(&started, "status")?, "running", "{started}");
    Ok(as_str(&started, "command_session_id")?.to_owned())
}

/// Live command-session count for one caller (empty `caller_id` counts all).
fn count_for(lease: &NodeLease<'_>, caller_id: &str) -> Result<i64> {
    let count = lease.call_ok(
        ops::API_V1_COMMAND_SESSION_COUNT,
        json!({"caller_id": caller_id}),
    )?;
    as_i64(&count, "count")
}

#[test]
fn cancel_workspace_runs_by_caller_id_discards_owner_and_spares_sibling() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let owner = lease.caller_id().to_owned();
    let sibling = format!("{owner}-sibling");

    // Two ephemeral runs for the owner caller, one for a sibling caller.
    let a = start_sleeping(&lease, None, "cancel-owner-a")?;
    let b = start_sleeping(&lease, None, "cancel-owner-b")?;
    let _s = start_sleeping(&lease, Some(&sibling), "cancel-sibling")?;
    assert_eq!(count_for(&lease, &owner)?, 2, "owner owns two ephemeral runs");
    assert_eq!(count_for(&lease, &sibling)?, 1, "sibling owns one run");

    let cancelled = lease.call_ok(
        ops::API_V1_CANCEL_WORKSPACE_RUNS_BY_CALLER,
        json!({"caller_id": owner}),
    )?;
    assert_eq!(
        as_i64(&cancelled, "cancelled_command_sessions")?,
        2,
        "per-caller cancel tears down exactly the owner's two runs: {cancelled}"
    );
    assert_eq!(
        cancelled["isolated_exited"],
        json!(false),
        "an ephemeral caller has no isolated workspace to exit: {cancelled}"
    );

    // The owner's runs are gone (lease caller == owner); the sibling is spared.
    wait_for_session_count(&lease, 0)?;
    assert_eq!(
        count_for(&lease, &sibling)?,
        1,
        "cancelling one caller must not touch a sibling caller's run"
    );

    // Cancel discards — no completion is parked for the torn-down sessions.
    let drained = lease.call_ok(
        ops::API_V1_COMMAND_COLLECT_COMPLETED,
        json!({"command_session_ids": [a, b]}),
    )?;
    assert!(
        array(&drained, "completions")?.is_empty(),
        "a cancelled session must not park a completion: {drained}"
    );

    // Tear the sibling down too and confirm every overlay lease released.
    let _ = lease.call(
        ops::API_V1_CANCEL_WORKSPACE_RUNS_BY_CALLER,
        json!({"caller_id": sibling}),
    );
    wait_for_active_leases(&lease, 0)?;
    Ok(())
}

#[test]
fn cancel_workspace_runs_sweeps_every_caller() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let other = format!("{}-other", lease.caller_id());

    start_sleeping(&lease, None, "sweep-a")?;
    start_sleeping(&lease, Some(&other), "sweep-b")?;
    assert_eq!(count_for(&lease, "")?, 2, "two runs across two callers are live");

    let swept = lease.call_ok(ops::API_V1_CANCEL_WORKSPACE_RUNS, json!({}))?;
    assert_eq!(
        as_i64(&swept, "cancelled_command_sessions")?,
        2,
        "the whole-sandbox sweep tears down every caller's runs: {swept}"
    );

    assert_eq!(count_for(&lease, "")?, 0, "no command session survives the sweep");
    wait_for_active_leases(&lease, 0)?;
    Ok(())
}
