//! Short `daemon.inflight` config reaps stale background invocation entries.

use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use anyhow::{bail, Result};
use eos_e2e_test::{unique_suffix, NodeLease};
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{as_bool, as_i64, live_pool_or_skip};

#[test]
fn inflight_ttl_reaper_cleanup() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let invocation_id = format!("daemon-ttl-{}", unique_suffix());
    let handle = spawn_long_background_exec(&lease, &invocation_id);

    wait_for_inflight_count(&lease, 1, Duration::from_secs(4))?;
    let wait_result = wait_for_inflight_count(&lease, 0, Duration::from_secs(12));
    if let Err(err) = wait_result {
        let _ = lease.call_ok(
            ops::API_V1_CANCEL,
            json!({"invocation_id": invocation_id.clone()}),
        );
        let _ = handle.join();
        return Err(err);
    }

    let _ = handle
        .join()
        .map_err(|_| anyhow::anyhow!("ttl background thread panicked"))?;
    let heartbeat = lease.call_ok(
        ops::API_V1_HEARTBEAT,
        json!({"invocation_ids": [invocation_id.clone()]}),
    )?;
    assert_eq!(
        as_i64(&heartbeat, "touched")?,
        0,
        "reaped invocation should be deregistered after request cleanup: {heartbeat}"
    );
    let cancel = lease.call_ok(ops::API_V1_CANCEL, json!({"invocation_id": invocation_id}))?;
    assert!(
        as_bool(&cancel, "already_done")?,
        "reaped invocation should cancel as already done: {cancel}"
    );
    Ok(())
}

fn spawn_long_background_exec(
    lease: &NodeLease<'_>,
    invocation_id: &str,
) -> JoinHandle<Result<Value>> {
    let client = lease.client().clone();
    let root = lease.root().to_owned();
    let caller_id = lease.caller_id().to_owned();
    let invocation_id = invocation_id.to_owned();
    thread::spawn(move || {
        Ok(client.request(
            ops::API_V1_EXEC_COMMAND,
            &invocation_id,
            &json!({
                "layer_stack_root": root,
                "caller_id": caller_id,
                "background": true,
                "cmd": "sleep 30",
                "yield_time_ms": 15000,
                "timeout_seconds": 60,}),
        )?)
    })
}

fn wait_for_inflight_count(
    lease: &NodeLease<'_>,
    expected: i64,
    timeout: Duration,
) -> Result<Value> {
    let deadline = Instant::now() + timeout;
    loop {
        let count = lease.call_ok(ops::API_V1_INFLIGHT_COUNT, json!({}))?;
        if as_i64(&count, "count")? == expected {
            return Ok(count);
        }
        if Instant::now() >= deadline {
            bail!("inflight_count did not reach {expected}: {count}");
        }
        thread::sleep(Duration::from_millis(50));
    }
}
