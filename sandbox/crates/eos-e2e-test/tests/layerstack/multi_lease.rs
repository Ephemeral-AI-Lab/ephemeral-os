//! Multiple non-adjacent leases each pin their own head barrier through squash.
//!
//! The e2e-reachable approximation of the squash gap formula ("n leased layers ->
//! n barriers + folded gaps"): two isolated sessions enter at non-adjacent heads,
//! then heavy public squash pressure runs between/after them. Each held head must
//! stay a frozen barrier (its pinned manifest never folds) while the public head
//! advances far past it and depth stays bounded — proving squash segments AROUND
//! every active lease, not just one. The exact arithmetic stays in the
//! `eos-layerstack` unit tests; this proves the live multi-lease invariant.

use anyhow::Result;
use eos_e2e_test::unique_suffix;
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{
    as_i64, as_str, live_pool_or_skip, reset_isolated_workspaces, wait_for_active_leases,
};

fn public_write(lease: &eos_e2e_test::NodeLease<'_>, version: usize) -> Result<()> {
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({
            "caller_id": "ml-public-writer",
            "path": "multi-lease/pub.txt",
            "content": format!("public-{version}\n"),
            "overwrite": true
        }),
    )?;
    Ok(())
}

fn pinned_version(lease: &eos_e2e_test::NodeLease<'_>, caller: &str) -> Result<i64> {
    let status = lease.call_ok(
        ops::API_ISOLATED_WORKSPACE_STATUS,
        json!({"caller_id": caller}),
    )?;
    as_i64(&status, "manifest_version")
}

#[test]
fn two_non_adjacent_leases_each_stay_pinned() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_workspaces(&lease);
    let caller_a = format!("ml-a-{}", unique_suffix());
    let caller_b = format!("ml-b-{}", unique_suffix());

    let enter_a = lease.call_ok(
        ops::API_ISOLATED_WORKSPACE_ENTER,
        json!({"caller_id": caller_a}),
    )?;
    let ver_a = as_i64(&enter_a, "manifest_version")?;
    let hash_a = as_str(&enter_a, "manifest_root_hash")?.to_owned();

    let body = (|| -> Result<()> {
        // First gap of public layers above A's pinned head.
        for version in 0..15 {
            public_write(&lease, version)?;
        }
        // B enters at a strictly later, non-adjacent head.
        let enter_b = lease.call_ok(
            ops::API_ISOLATED_WORKSPACE_ENTER,
            json!({"caller_id": caller_b}),
        )?;
        let ver_b = as_i64(&enter_b, "manifest_version")?;
        let hash_b = as_str(&enter_b, "manifest_root_hash")?.to_owned();
        assert!(
            ver_b > ver_a,
            "B must pin a later head than A: a={ver_a} b={ver_b}"
        );
        // Second gap: more squash pressure now spans BOTH held barriers.
        for version in 15..30 {
            public_write(&lease, version)?;
        }

        let metrics = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
        assert_eq!(
            as_i64(&metrics, "active_leases")?,
            2,
            "both non-adjacent leases must be active: {metrics}"
        );
        // Squash ran (depth bounded far below the 30 public writes) yet neither
        // held head folded: both pins are frozen at their enter versions.
        assert!(
            as_i64(&metrics, "manifest_depth")? <= 20,
            "auto-squash must keep depth bounded under multi-lease pressure: {metrics}"
        );
        assert_eq!(pinned_version(&lease, &caller_a)?, ver_a, "A pin moved");
        assert_eq!(pinned_version(&lease, &caller_b)?, ver_b, "B pin moved");
        let status_a = lease.call_ok(
            ops::API_ISOLATED_WORKSPACE_STATUS,
            json!({"caller_id": caller_a}),
        )?;
        assert_eq!(as_str(&status_a, "manifest_root_hash")?, hash_a, "A hash moved");
        let status_b = lease.call_ok(
            ops::API_ISOLATED_WORKSPACE_STATUS,
            json!({"caller_id": caller_b}),
        )?;
        assert_eq!(as_str(&status_b, "manifest_root_hash")?, hash_b, "B hash moved");
        Ok(())
    })();

    let _ = lease.call(
        ops::API_ISOLATED_WORKSPACE_EXIT,
        json!({"caller_id": caller_a}),
    );
    let _ = lease.call(
        ops::API_ISOLATED_WORKSPACE_EXIT,
        json!({"caller_id": caller_b}),
    );
    body?;
    let released = wait_for_active_leases(&lease, 0)?;
    assert_eq!(as_i64(&released, "active_leases")?, 0, "{released}");
    Ok(())
}

#[test]
fn squash_with_held_lease_keeps_layer_dirs_bounded() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_workspaces(&lease);
    let caller = format!("ml-gc-{}", unique_suffix());
    lease.call_ok(
        ops::API_ISOLATED_WORKSPACE_ENTER,
        json!({"caller_id": caller}),
    )?;

    let body = (|| -> Result<Value> {
        for version in 0..30 {
            public_write(&lease, version)?;
        }
        let held = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
        // Even while a lease pins an early head, on-disk layer dirs stay bounded
        // (squash folds the unpinned runs) — NOT one dir per public write.
        assert!(
            as_i64(&held, "layer_dirs")? < 30,
            "held-lease squash must bound on-disk layer dirs: {held}"
        );
        Ok(held)
    })();

    let _ = lease.call(
        ops::API_ISOLATED_WORKSPACE_EXIT,
        json!({"caller_id": caller}),
    );
    let held = body?;
    // After the lease releases, the pinned-but-superseded tail is GC'd: layer dirs
    // collapse toward the live manifest (the real orphan/GC oracle).
    let after = wait_for_active_leases(&lease, 0)?;
    assert!(
        as_i64(&after, "layer_dirs")? <= as_i64(&after, "referenced_layers")? + 4,
        "released lease tail must be reclaimed (layer_dirs ~ referenced): {after}"
    );
    assert!(
        as_i64(&after, "layer_dirs")? <= as_i64(&held, "layer_dirs")?,
        "GC must not grow on-disk layer dirs after release: held={held} after={after}"
    );
    Ok(())
}
