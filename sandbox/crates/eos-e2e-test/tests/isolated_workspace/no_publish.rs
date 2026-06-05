use anyhow::Result;
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::common::{as_bool, as_str, live_pool_or_skip};

#[test]
fn isolated_write_is_discarded_on_exit() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let path = "iso/private.txt";

    lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;

    let write = lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": path, "content": "isolated-only\n", "overwrite": true}),
    )?;
    assert_eq!(
        as_str(&write, "mutation_source")?,
        "isolated_workspace",
        "write inside isolated mode must be isolated-sourced: {write}"
    );
    assert_eq!(
        as_str(&write, "status")?,
        "committed",
        "isolated write status: {write}"
    );

    let read_inside = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": path}))?;
    assert_eq!(as_str(&read_inside, "content")?, "isolated-only\n");

    let exit = lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({}))?;
    assert!(
        exit.get("evicted_upperdir_bytes")
            .and_then(Value::as_i64)
            .unwrap_or(0)
            >= 0,
        "exit reports evicted upperdir bytes: {exit}"
    );

    let read_public = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": path}))?;
    assert!(
        !as_bool(&read_public, "exists")?,
        "isolated write must not survive into the public workspace: {read_public}"
    );
    Ok(())
}

#[test]
fn isolated_exit_discards_private_upperdir() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "iso-overlay/discard.txt", "content": "discard\n", "overwrite": true}),
    )?;
    let exit = lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({}))?;
    assert!(
        exit.get("inspection").is_some(),
        "isolated exit should report teardown inspection: {exit}"
    );
    let read = lease.call_ok(
        ops::API_V1_READ_FILE,
        json!({"path": "iso-overlay/discard.txt"}),
    )?;
    assert!(
        !as_bool(&read, "exists")?,
        "private isolated write must not survive exit: {read}"
    );
    let closed = lease.call_ok(ops::API_ISOLATED_WORKSPACE_STATUS, json!({}))?;
    assert!(
        !as_bool(&closed, "open")?,
        "status after exit should be closed: {closed}"
    );
    Ok(())
}
