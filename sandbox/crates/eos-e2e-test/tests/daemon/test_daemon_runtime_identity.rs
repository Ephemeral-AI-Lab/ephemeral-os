//! Daemon identity + per-response dispatch timings.

use anyhow::Result;
use eos_daemon::wire::ops;
use serde_json::{json, Value};

use crate::support::{array, as_i64, as_str, live_pool_or_skip};

#[test]
fn runtime_ready_exposes_daemon_identity() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let ready = lease.call_ok(ops::SANDBOX_RUNTIME_READY, json!({}))?;
    // The daemon is the in-sandbox parent process: ready exposes its pid + uptime.
    assert!(
        as_i64(&ready, "daemon_pid")? > 0,
        "runtime.ready must expose a positive daemon_pid: {ready}"
    );
    assert!(
        ready
            .get("uptime_s")
            .and_then(Value::as_f64)
            .is_some_and(|uptime| uptime >= 0.0),
        "runtime.ready must expose a non-negative uptime_s: {ready}"
    );
    for probe in array(&ready, "probes")? {
        assert_eq!(
            probe.get("status").and_then(Value::as_str),
            Some("ok"),
            "every readiness probe must report ok: {probe}"
        );
    }
    Ok(())
}

#[test]
fn every_response_carries_dispatch_timings() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let ready = lease.call_ok(ops::SANDBOX_RUNTIME_READY, json!({}))?;
    let timings = ready
        .get("timings")
        .and_then(Value::as_object)
        .expect("response must carry a timings map");
    for key in [
        "runtime.dispatch_s",
        "runtime.read_request_s",
        "runtime.boot_to_dispatch_s",
    ] {
        assert!(
            timings.get(key).and_then(Value::as_f64).is_some(),
            "every response must report {key}: {ready}"
        );
    }
    // Dispatch timings ride even error envelopes.
    let bogus = lease.call("api.totally.bogus.op", json!({}))?;
    assert_eq!(as_str(&bogus, "success").unwrap_or("false"), "false");
    assert!(
        bogus
            .get("timings")
            .and_then(|timings| timings.get("runtime.dispatch_s"))
            .and_then(Value::as_f64)
            .is_some(),
        "error envelopes must still carry dispatch timing: {bogus}"
    );
    Ok(())
}
