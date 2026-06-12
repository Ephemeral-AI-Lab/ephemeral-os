//! Daemon identity + per-response dispatch timings.

use anyhow::Result;
use eos_operation::core::catalog;
use serde_json::{json, Value};

use crate::support::{array, as_i64, envelope_meta, envelope_result, live_pool_or_skip};

#[test]
fn runtime_ready_exposes_daemon_identity() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let ready_wire = lease.call(catalog::SANDBOX_RUNTIME_READY, json!({}))?;
    assert_eq!(
        ready_wire["status"], "ok",
        "runtime.ready uses ok envelope: {ready_wire}"
    );
    let ready = envelope_result(&ready_wire)?;
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
fn every_response_carries_runtime_envelope_meta() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let ready = lease.call(catalog::SANDBOX_RUNTIME_READY, json!({}))?;
    assert_eq!(
        ready["status"], "ok",
        "runtime.ready uses ok envelope: {ready}"
    );
    let meta = envelope_meta(&ready)?;
    assert_eq!(meta.op, catalog::SANDBOX_RUNTIME_READY);
    assert!(
        meta.duration_ms >= 0.0,
        "ok responses must carry nonnegative duration meta: {ready}"
    );
    assert!(
        meta.steps
            .iter()
            .any(|step| step.kind == "runtime.dispatch"),
        "ok responses must carry dispatch step meta: {ready}"
    );

    // Dispatch meta rides even error responses.
    let bogus = lease.call("api.totally.bogus.op", json!({}))?;
    assert_eq!(
        bogus["status"], "error",
        "unknown op uses error envelope: {bogus}"
    );
    let meta = envelope_meta(&bogus)?;
    assert_eq!(meta.op, "api.totally.bogus.op");
    assert!(
        meta.steps
            .iter()
            .any(|step| step.kind == "runtime.dispatch"),
        "error responses must still carry dispatch step meta: {bogus}"
    );
    Ok(())
}
