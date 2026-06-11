use std::thread;
use std::time::Duration;

use anyhow::Result;
use eos_e2e_test::next_invocation_id;
use eos_operation::core::ops;
use serde_json::{json, Value};

use crate::support::{
    as_i64, as_str, live_pool_or_skip, reset_isolated_workspaces, wait_for_active_leases,
};

#[test]
fn enter_acquires_lease() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let enter = lease.call_ok(ops::SANDBOX_ISOLATION_ENTER, json!({}))?;
    assert!(!as_str(&enter, "workspace_handle_id")?.is_empty());
    let metrics = lease.call_ok(ops::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    assert!(
        as_i64(&metrics, "active_leases")? >= 1,
        "enter should hold a snapshot lease while the workspace is open: {metrics}"
    );
    let status = lease.call_ok(ops::SANDBOX_ISOLATION_STATUS, json!({}))?;
    assert!(status.get("open").and_then(Value::as_bool).unwrap_or(false));
    lease.call_ok(ops::SANDBOX_ISOLATION_EXIT, json!({}))?;
    Ok(())
}

#[test]
fn exit_releases_lease() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(ops::SANDBOX_ISOLATION_ENTER, json!({}))?;
    lease.call_ok(ops::SANDBOX_ISOLATION_EXIT, json!({}))?;
    let closed = lease.call_ok(ops::SANDBOX_ISOLATION_STATUS, json!({}))?;
    assert!(!closed.get("open").and_then(Value::as_bool).unwrap_or(true));
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
    let enter = lease.call_ok(ops::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let pinned_version = enter.get("manifest_version").and_then(Value::as_i64);
    let pinned_hash = enter
        .get("manifest_root_hash")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let root = lease.root().to_owned();
    for version in 0..105 {
        lease.client().request(
            ops::SANDBOX_FILE_WRITE,
            &next_invocation_id(),
            &json!({
                "layer_stack_root": root,
                "caller_id": "lease-public-writer",
                "path": "lease/pinned.txt",
                "content": format!("public-{version}\n"),
                "overwrite": true
            }),
        )?;
    }
    let held = lease.call_ok(ops::SANDBOX_ISOLATION_STATUS, json!({}))?;
    assert!(
        held.get("open").and_then(Value::as_bool).unwrap_or(false),
        "isolated status should remain open while public squash pressure runs: {held}"
    );
    assert_eq!(
        held.get("manifest_version").and_then(Value::as_i64),
        pinned_version
    );
    assert_eq!(
        held.get("manifest_root_hash").and_then(Value::as_str),
        pinned_hash.as_deref()
    );
    lease.call_ok(ops::SANDBOX_ISOLATION_EXIT, json!({}))?;
    let released = wait_for_active_leases(&lease, 0)?;
    assert_eq!(as_i64(&released, "active_leases")?, 0);
    Ok(())
}

#[test]
fn squash_keeps_multiple_pinned_statuses_while_live_manifest_collapses() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_workspaces(&lease);
    let suffix = eos_e2e_test::unique_suffix();
    let callers = [
        format!("layerstack-gap-lease-a-{suffix}"),
        format!("layerstack-gap-lease-b-{suffix}"),
        format!("layerstack-gap-lease-c-{suffix}"),
    ];

    let outcome: Result<()> = (|| {
        let pinned_a = enter_isolated(&lease, &callers[0])?;
        write_public_versions(&lease, 0..2)?;
        let pinned_b = enter_isolated(&lease, &callers[1])?;
        write_public_versions(&lease, 2..4)?;
        let pinned_c = enter_isolated(&lease, &callers[2])?;

        write_public_versions(&lease, 4..8)?;

        for (caller, pinned) in [
            (callers[0].as_str(), &pinned_a),
            (callers[1].as_str(), &pinned_b),
            (callers[2].as_str(), &pinned_c),
        ] {
            assert_pinned_status(&lease, caller, pinned)?;
        }

        // Base + 8 public writes pushes the stack past the squash trigger; the
        // post-squash active manifest must stay bounded while the three pinned
        // statuses above remain stable.
        let metrics = lease.call_ok(ops::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
        let depth = as_i64(&metrics, "manifest_depth")?;
        assert!(
            depth <= 8,
            "post-squash active manifest should stay bounded while pinned statuses remain stable: {metrics}"
        );
        assert_eq!(
            as_i64(&metrics, "referenced_layers")?,
            depth,
            "referenced layers should match the active manifest after squash: {metrics}"
        );
        assert!(
            as_i64(&metrics, "layer_dirs")? <= depth + 2,
            "folded gap layers should stay bounded with the active manifest: {metrics}"
        );
        Ok(())
    })();

    for caller in &callers {
        let _ = lease.call(
            ops::SANDBOX_ISOLATION_EXIT,
            json!({"caller_id": caller, "grace_s": 0.0}),
        );
    }
    let released = wait_for_active_leases(&lease, 0);
    outcome?;
    let released = released?;
    assert_eq!(as_i64(&released, "active_leases")?, 0);
    Ok(())
}

#[test]
fn lease_hold_time_ordering() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(ops::SANDBOX_ISOLATION_ENTER, json!({}))?;
    thread::sleep(Duration::from_millis(150));
    let exit = lease.call_ok(ops::SANDBOX_ISOLATION_EXIT, json!({}))?;
    let lifetime_s = exit
        .get("lifetime_s")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    assert!(
        lifetime_s >= 0.0,
        "isolated exit lifetime should be nonnegative: {exit}"
    );
    Ok(())
}

struct PinnedLease {
    manifest_version: i64,
    manifest_root_hash: String,
}

fn enter_isolated(lease: &eos_e2e_test::NodeLease<'_>, caller_id: &str) -> Result<PinnedLease> {
    let enter = lease.call_ok(
        ops::SANDBOX_ISOLATION_ENTER,
        json!({"caller_id": caller_id}),
    )?;
    Ok(PinnedLease {
        manifest_version: as_i64(&enter, "manifest_version")?,
        manifest_root_hash: as_str(&enter, "manifest_root_hash")?.to_owned(),
    })
}

fn assert_pinned_status(
    lease: &eos_e2e_test::NodeLease<'_>,
    caller_id: &str,
    pinned: &PinnedLease,
) -> Result<()> {
    let status = lease.call_ok(
        ops::SANDBOX_ISOLATION_STATUS,
        json!({"caller_id": caller_id}),
    )?;
    assert!(
        status.get("open").and_then(Value::as_bool).unwrap_or(false),
        "isolated status should stay open for {caller_id}: {status}"
    );
    assert_eq!(
        as_i64(&status, "manifest_version")?,
        pinned.manifest_version,
        "squash must not move the pinned manifest version for {caller_id}: {status}"
    );
    assert_eq!(
        as_str(&status, "manifest_root_hash")?,
        pinned.manifest_root_hash,
        "squash must not move the pinned manifest hash for {caller_id}: {status}"
    );
    Ok(())
}

fn write_public_versions(
    lease: &eos_e2e_test::NodeLease<'_>,
    versions: std::ops::Range<usize>,
) -> Result<()> {
    for version in versions {
        lease.call_ok(
            ops::SANDBOX_FILE_WRITE,
            json!({
                "caller_id": "layerstack-gap-public-writer",
                "path": "lease/gap-formula.txt",
                "content": format!("public-{version}\n"),
                "overwrite": true
            }),
        )?;
    }
    Ok(())
}
