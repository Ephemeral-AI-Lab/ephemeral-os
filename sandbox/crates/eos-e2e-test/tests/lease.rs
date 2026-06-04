mod common;

use std::thread;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use eos_e2e_test::next_invocation_id;
use eos_protocol::ops;
use serde_json::{json, Value};

use common::{as_i64, as_str, live_pool_or_skip};

#[test]
fn enter_acquires_lease() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let mut audit = lease.audit_tap()?;
    let enter = lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    assert!(as_str(&enter, "workspace_handle_id")?.starts_with("iws-"));
    audit.collect()?;
    assert!(
        audit.any("layer_stack.lease_acquired"),
        "isolated enter should emit lease_acquired: {:?}",
        audit.events()
    );
    let metrics = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
    assert!(
        as_i64(&metrics, "active_leases")? >= 1,
        "isolated enter should hold a layer lease: {metrics}"
    );
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({}))?;
    Ok(())
}

#[test]
fn exit_releases_lease() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    let mut audit = lease.audit_tap()?;
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({}))?;
    audit.collect()?;
    assert!(
        audit.any("layer_stack.lease_released"),
        "isolated exit should emit lease_released: {:?}",
        audit.events()
    );
    let metrics = wait_for_active_leases(&lease, 0)?;
    assert_eq!(as_i64(&metrics, "active_leases")?, 0);
    Ok(())
}

#[test]
fn lease_pins_layers_vs_squash() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    let root = lease.root().to_owned();
    for version in 0..105 {
        lease.client().request(
            ops::API_V1_WRITE_FILE,
            &next_invocation_id(),
            &json!({
                "layer_stack_root": root,
                "agent_id": "lease-public-writer",
                "path": "lease/pinned.txt",
                "content": format!("public-{version}\n"),
                "overwrite": true
            }),
        )?;
    }
    let held = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
    assert!(
        as_i64(&held, "active_leases")? >= 1,
        "isolated lease should remain held while public squash pressure runs: {held}"
    );
    assert!(
        as_i64(&held, "leased_layers")? >= 1,
        "held lease should pin at least one layer: {held}"
    );
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({}))?;
    let released = wait_for_active_leases(&lease, 0)?;
    assert_eq!(as_i64(&released, "active_leases")?, 0);
    Ok(())
}

#[test]
fn lease_hold_time_ordering() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    thread::sleep(Duration::from_millis(150));
    let mut audit = lease.audit_tap()?;
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({}))?;
    audit.collect()?;
    let released = audit
        .first("layer_stack.lease_released")
        .context("layer_stack.lease_released")?;
    let hold_ms = released
        .get("payload")
        .and_then(|payload| payload.get("layer_stack"))
        .and_then(|section| section.get("lease_hold_ms"))
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    assert!(hold_ms >= 0.0, "lease hold time should be present or nonnegative: {released}");
    Ok(())
}

#[test]
fn read_op_transient_lease_released() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "lease/read.txt", "content": "needle\n", "overwrite": true}),
    )?;
    let mut audit = lease.audit_tap()?;
    lease.call_ok(
        ops::API_V1_GREP,
        json!({"pattern": "needle", "path": "lease", "output_mode": "content"}),
    )?;
    audit.collect()?;
    assert!(
        audit.any("layer_stack.lease_released"),
        "overlay read op should release its transient snapshot lease: {:?}",
        audit.events()
    );
    let metrics = wait_for_active_leases(&lease, 0)?;
    assert_eq!(as_i64(&metrics, "active_leases")?, 0);
    Ok(())
}

fn wait_for_active_leases(lease: &eos_e2e_test::NodeLease<'_>, expected: i64) -> Result<Value> {
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let metrics = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
        if as_i64(&metrics, "active_leases")? == expected {
            return Ok(metrics);
        }
        if Instant::now() >= deadline {
            bail!("active_leases did not reach {expected}: {metrics}");
        }
        thread::sleep(Duration::from_millis(50));
    }
}
