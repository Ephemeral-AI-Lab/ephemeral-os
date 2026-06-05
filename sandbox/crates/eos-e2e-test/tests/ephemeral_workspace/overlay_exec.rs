use anyhow::{Context, Result};
use eos_e2e_test::audit::section;
use eos_e2e_test::unique_suffix;
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{array, as_i64, as_str, live_pool_or_skip, stdout, wait_for_active_leases};

/// Read a nested `timings.<key>` number from a response.
fn timing_f64(value: &Value, key: &str) -> Option<f64> {
    value
        .get("timings")
        .and_then(|timings| timings.get(key))
        .and_then(Value::as_f64)
}

#[test]
fn exec_write_outside_workspace_is_not_captured() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let marker = format!("/tmp/eos_outside_{}", unique_suffix().replace('-', "_"));
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!("mkdir -p scope_in && printf inside > scope_in/inside.txt && printf outside > {marker}"),
            "yield_time_ms": 1000,
            "timeout_seconds": 10,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&exec, "status")?, "ok", "{exec}");
    // The overlay captures only the upperdir over workspace_root: the in-workspace
    // path is published, the /tmp write is invisible to OCC (merged to the shared
    // container FS directly).
    let changed = array(&exec, "changed_paths")?;
    assert!(
        changed
            .iter()
            .any(|path| path.as_str() == Some("scope_in/inside.txt")),
        "in-workspace write must be captured: {exec}"
    );
    assert!(
        changed
            .iter()
            .all(|path| !path.as_str().unwrap_or_default().contains("tmp")),
        "an out-of-workspace /tmp write must not appear in changed_paths: {exec}"
    );
    // Secondary: the outside write landed on the real container /tmp and a fresh
    // ephemeral exec re-derived over / still sees it.
    let read_back = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({"cmd": format!("cat {marker}"), "yield_time_ms": 1000, "timeout_seconds": 10}),
    )?;
    assert_eq!(stdout(&read_back), "outside", "{read_back}");
    Ok(())
}

#[test]
fn foreground_exec_recycles_overlay_scratch() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let mut audit = lease.audit_tap()?;
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "mkdir -p auditscope && printf x > auditscope/a.txt",
            "yield_time_ms": 1000,
            "timeout_seconds": 10,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&exec, "status")?, "ok", "{exec}");
    assert!(exec.get("command_session_id").is_none(), "{exec}");
    audit.collect()?;
    // The overlay scratch (upperdir + workdir) is torn down on finalize and the
    // lease is released — observable as the recycle audit + active_leases back to 0.
    let cleanup = audit
        .first("overlay_workspace.cleanup")
        .context("foreground exec must emit overlay_workspace.cleanup")?;
    assert_eq!(
        section(cleanup, "overlay_workspace")
            .and_then(|overlay| overlay.get("scratch_removed"))
            .and_then(Value::as_bool),
        Some(true),
        "overlay scratch must be recycled on finalize: {cleanup}"
    );
    assert!(
        audit.any("layer_stack.lease_released"),
        "completed overlay exec must release its lease: {:?}",
        audit.events()
    );
    let metrics = wait_for_active_leases(&lease, 0)?;
    assert_eq!(as_i64(&metrics, "active_leases")?, 0, "{metrics}");
    Ok(())
}

#[test]
fn exec_upperdir_captures_only_the_delta() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // Seed a 200KB base file via the fast path (lands in the lower layer stack).
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "perf/base_big.txt", "content": "x".repeat(200_000), "overwrite": true}),
    )?;
    // A tiny overlay write must capture only its own delta — the overlay does NOT
    // copy the 200KB base into the upperdir (the O(1)-lowerdir-disk property).
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "printf SMALL > perf/delta.txt",
            "yield_time_ms": 1000,
            "timeout_seconds": 10,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&exec, "status")?, "ok", "{exec}");
    let upperdir_bytes = timing_f64(&exec, "resource.command_exec.upperdir_tree_bytes")
        .context("exec response must carry resource.command_exec.upperdir_tree_bytes")?;
    assert!(
        upperdir_bytes < 100_000.0,
        "upperdir delta must not copy the 200KB base (got {upperdir_bytes} bytes): {exec}"
    );
    assert!(
        array(&exec, "changed_paths")?
            .iter()
            .any(|path| path.as_str() == Some("perf/delta.txt")),
        "delta write must be captured: {exec}"
    );
    Ok(())
}

#[test]
fn exec_overlay_mount_publishes_changed_paths() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "mkdir -p overlay && printf from-overlay > overlay/exec.txt",
            "yield_time_ms": 1000,
            "timeout_seconds": 10,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&exec, "status")?, "ok");
    assert_eq!(as_i64(&exec, "exit_code")?, 0);
    assert!(
        array(&exec, "changed_paths")?
            .iter()
            .any(|path| path.as_str() == Some("overlay/exec.txt")),
        "exec overlay should publish captured upperdir paths: {exec}"
    );
    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": "overlay/exec.txt"}))?;
    assert_eq!(as_str(&read, "content")?, "from-overlay");
    Ok(())
}
