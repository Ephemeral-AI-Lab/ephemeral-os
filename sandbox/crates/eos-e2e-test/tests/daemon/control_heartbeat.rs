//! `api.v1.heartbeat` `touched` count distinguishes live ids from bogus ones.

use std::time::{Duration, Instant};

use anyhow::{bail, Result};
use eos_protocol::ops;
use serde_json::json;

use crate::spawn_inflight_exec;
use crate::support::{as_i64, live_pool_or_skip};

#[test]
fn heartbeat_touched_counts_only_bogus_as_zero() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // Deterministic: no id is registered, so nothing is touched.
    let heartbeat = lease.call_ok(
        ops::API_V1_HEARTBEAT,
        json!({"invocation_ids": ["nope-1", "nope-2"]}),
    )?;
    assert_eq!(
        as_i64(&heartbeat, "touched")?,
        0,
        "bogus ids must touch nothing: {heartbeat}"
    );
    Ok(())
}

#[test]
fn heartbeat_touched_distinguishes_live_from_bogus() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let invocation_id = "daemon-hb-iid";
    let handle = spawn_inflight_exec(&lease, invocation_id);

    let deadline = Instant::now() + Duration::from_secs(4);
    loop {
        let count = lease.call_ok(ops::API_V1_INFLIGHT_COUNT, json!({}))?;
        if as_i64(&count, "count")? >= 1 {
            break;
        }
        if Instant::now() >= deadline {
            let _ = handle.join();
            bail!("background invocation never registered: {count}");
        }
        std::thread::sleep(Duration::from_millis(50));
    }

    let heartbeat = lease.call_ok(
        ops::API_V1_HEARTBEAT,
        json!({"invocation_ids": [invocation_id, "definitely-not-registered"]}),
    )?;
    let touched = as_i64(&heartbeat, "touched")?;
    let _ = handle.join();
    assert_eq!(
        touched, 1,
        "exactly the one live id must be touched: {heartbeat}"
    );
    Ok(())
}
