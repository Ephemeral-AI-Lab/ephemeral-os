//! Direct workspace-op edge cases and error catalog.
//!
//! Fills the deterministic response-oracle gaps the broad smoke test does not
//! cover: missing-file reads, the edit error catalog (anchor-not-found /
//! count-mismatch), and create-only write conflicts. All assert purely on the op
//! response payload.

use anyhow::Result;
use eos_daemon::wire::ops;
use serde_json::{json, Value};

use crate::support::{as_bool, conflict_message, live_pool_or_skip};

#[test]
fn read_nonexistent_reports_absent() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": "does/not/exist.txt"}))?;
    assert!(
        !as_bool(&read, "exists")?,
        "missing file must report exists=false: {read}"
    );
    Ok(())
}

#[test]
fn edit_error_catalog_anchor_not_found_and_count_mismatch() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;

    // anchor-not-found
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "edit/a.txt", "content": "hello world\n", "overwrite": true}),
    )?;
    let not_found = lease.call(
        ops::API_V1_EDIT_FILE,
        json!({"path": "edit/a.txt", "edits": [{"old_text": "ABSENT", "new_text": "x", "replace_all": false}]}),
    )?;
    assert!(
        conflict_message(&not_found).contains("anchor not found"),
        "missing anchor must surface NotFound: {not_found}"
    );

    // count-mismatch (anchor appears twice, replace_all=false)
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "edit/b.txt", "content": "dup dup\n", "overwrite": true}),
    )?;
    let mismatch = lease.call(
        ops::API_V1_EDIT_FILE,
        json!({"path": "edit/b.txt", "edits": [{"old_text": "dup", "new_text": "x", "replace_all": false}]}),
    )?;
    assert!(
        conflict_message(&mismatch).contains("count mismatch"),
        "ambiguous anchor must surface CountMismatch: {mismatch}"
    );
    Ok(())
}

#[test]
fn write_create_only_conflict() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "co/only.txt", "content": "first\n", "overwrite": true}),
    )?;
    let rejected = lease.call(
        ops::API_V1_WRITE_FILE,
        json!({"path": "co/only.txt", "content": "second\n", "overwrite": false}),
    )?;
    let reason = rejected
        .get("conflict")
        .and_then(|c| c.get("reason"))
        .and_then(Value::as_str)
        .unwrap_or("");
    assert_eq!(
        reason, "create_only_existing",
        "overwrite=false on an existing file must be a create-only conflict: {rejected}"
    );
    Ok(())
}
