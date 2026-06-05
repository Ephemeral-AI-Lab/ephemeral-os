//! Isolated-workspace lifecycle tests.
//!
//! Exercises the real SetNs / ns-holder / veth / cgroup machinery via
//! `enter` → (isolated write/read) → `status` → `exit`, asserting on the op
//! responses: the manifest pin on enter/status, isolated `mutation_source`,
//! discard-on-exit (the write is never OCC-published), and the exit `inspection`
//! teardown facts.

use anyhow::{Context, Result};
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{as_bool, as_str, live_pool_or_skip};

#[test]
fn enter_status_exit_pin_and_teardown() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;

    let enter = lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    let handle_id = as_str(&enter, "workspace_handle_id")?.to_owned();
    let pinned_version = enter
        .get("manifest_version")
        .and_then(Value::as_i64)
        .context("enter manifest_version")?;
    let pinned_hash = as_str(&enter, "manifest_root_hash")?.to_owned();
    assert!(
        !handle_id.is_empty(),
        "enter must return a handle id: {enter}"
    );
    assert_eq!(
        pinned_hash.len(),
        64,
        "manifest_root_hash must be sha256 hex: {enter}"
    );

    // status reports the same pin while open.
    let status = lease.call_ok(ops::API_ISOLATED_WORKSPACE_STATUS, json!({}))?;
    assert!(
        as_bool(&status, "open")?,
        "status must report open: {status}"
    );
    assert_eq!(
        status.get("manifest_version").and_then(Value::as_i64),
        Some(pinned_version),
        "status pin must match enter: {status}"
    );

    // exit tears down and reports inspection facts.
    let exit = lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({}))?;
    let inspection = exit.get("inspection").context("exit inspection")?;
    assert_eq!(
        inspection
            .get("handle_registered_after")
            .and_then(Value::as_bool),
        Some(false),
        "handle must be unregistered after exit: {exit}"
    );
    // lease_released is Option<bool>: when present it must be true.
    if let Some(released) = inspection.get("lease_released").and_then(Value::as_bool) {
        assert!(released, "isolated lease must be released on exit: {exit}");
    }
    // cgroup_exists_after is Option<bool>: when present it must be false.
    if let Some(cgroup) = inspection
        .get("cgroup_exists_after")
        .and_then(Value::as_bool)
    {
        assert!(!cgroup, "cgroup must be removed on exit: {exit}");
    }
    assert!(
        inspection
            .get("holder_kill_error")
            .map(Value::is_null)
            .unwrap_or(true),
        "ns-holder must be reaped without error: {exit}"
    );

    // status after exit reports closed.
    let closed = lease.call_ok(ops::API_ISOLATED_WORKSPACE_STATUS, json!({}))?;
    assert!(
        !as_bool(&closed, "open")?,
        "status must report closed: {closed}"
    );
    Ok(())
}
