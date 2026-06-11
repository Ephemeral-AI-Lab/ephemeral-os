//! `api.v1.cancel` envelope: unknown-id done envelope + live in-flight cancel.

use std::time::{Duration, Instant};

use anyhow::{bail, Result};
use eos_e2e_test::unique_suffix;
use eos_operation::core::ops;
use serde_json::json;

use crate::spawn_inflight_exec;
use crate::support::{as_bool, as_i64, as_str, live_pool_or_skip};

#[test]
fn cancel_unknown_invocation_returns_done_envelope() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let invocation_id = format!("never-registered-{}", unique_suffix());
    let cancel = lease.call_ok(
        ops::SANDBOX_CALL_CANCEL,
        json!({"invocation_id": invocation_id.clone()}),
    )?;
    // Cancelling an id the registry never saw is a deterministic "already done".
    assert!(
        !as_bool(&cancel, "cancelled")?,
        "an unknown id was not cancelled: {cancel}"
    );
    assert!(
        as_bool(&cancel, "already_done")?,
        "an unknown id must report already_done: {cancel}"
    );
    assert!(
        as_bool(&cancel, "cleanup_done")?,
        "an unknown id must report cleanup_done: {cancel}"
    );
    assert_eq!(
        as_str(&cancel, "invocation_id")?,
        invocation_id,
        "cancel must echo the requested invocation_id: {cancel}"
    );
    Ok(())
}

#[test]
fn live_cancel_of_inflight_sets_cancelled() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let invocation_id = "daemon-cancel-iid";
    let handle = spawn_inflight_exec(&lease, invocation_id);

    // Only cancel once the registry actually holds the entry (gate on inflight).
    let deadline = Instant::now() + Duration::from_secs(4);
    loop {
        let count = lease.call_ok(ops::SANDBOX_CALL_COUNT, json!({}))?;
        if as_i64(&count, "count")? >= 1 {
            break;
        }
        if Instant::now() >= deadline {
            let _ = handle.join();
            bail!("background invocation never registered as in-flight: {count}");
        }
        std::thread::sleep(Duration::from_millis(50));
    }

    let cancel = lease.call_ok(
        ops::SANDBOX_CALL_CANCEL,
        json!({"invocation_id": invocation_id}),
    )?;
    let cancelled = as_bool(&cancel, "cancelled")?;
    // Join before the lease drops so the background request never outlives the node.
    let _ = handle.join();
    assert!(
        cancelled,
        "cancelling a registered in-flight invocation must report cancelled: {cancel}"
    );
    Ok(())
}
