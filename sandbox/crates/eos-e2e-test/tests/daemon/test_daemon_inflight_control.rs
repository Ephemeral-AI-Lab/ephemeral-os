//! `sandbox.call.count` observes real background invocations on one daemon.

use std::time::{Duration, Instant};

use anyhow::{bail, Result};
use eos_operation::core::catalog;
use serde_json::json;

use crate::spawn_inflight_exec;
use crate::support::{as_i64, live_pool_or_skip};

#[test]
fn inflight_count_observes_concurrent_background_invocations() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // Two concurrent background invocations (distinct ids, same caller) prove both
    // wire-level inflight accounting AND concurrent dispatch on one daemon.
    let handles = [
        spawn_inflight_exec(&lease, "daemon-inflight-1"),
        spawn_inflight_exec(&lease, "daemon-inflight-2"),
    ];

    let deadline = Instant::now() + Duration::from_secs(4);
    let mut peak = 0;
    loop {
        let count = lease.call_ok(catalog::SANDBOX_CALL_COUNT, json!({}))?;
        peak = peak.max(as_i64(&count, "count")?);
        if peak >= 2 || Instant::now() >= deadline {
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    for handle in handles {
        let _ = handle.join();
    }
    if peak < 2 {
        bail!("inflight_count never reached the 2 concurrent background invocations (peak {peak})");
    }
    Ok(())
}
