use anyhow::Result;
use eos_daemon::wire::ops;
use eos_e2e_test::cas::looks_like_sha256;
use serde_json::{json, Value};

use crate::support::{as_bool, as_i64, as_str, live_pool_or_skip};

#[test]
fn setup_readiness_and_metrics_are_protocol_visible() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;

    let ready = lease.call_ok(ops::SANDBOX_RUNTIME_READY, json!({}))?;
    assert!(as_bool(&ready, "success")?);
    assert!(as_bool(&ready, "ready")?);

    let heartbeat = lease.call_ok(ops::SANDBOX_CALL_HEARTBEAT, json!({"invocation_ids": []}))?;
    assert!(as_bool(&heartbeat, "success")?);

    let binding = lease.call_ok(ops::SANDBOX_CHECKPOINT_BINDING, json!({}))?;
    assert_eq!(
        binding["binding"]["workspace_root"],
        Value::String(lease.workspace_root().to_owned())
    );

    let metrics = lease.call_ok(ops::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    assert!(as_bool(&metrics, "workspace_bound")?);
    assert_eq!(as_i64(&metrics, "active_leases")?, 0);

    let ensure = lease.call_ok(
        ops::SANDBOX_CHECKPOINT_ENSURE_BASE,
        json!({"workspace_root": lease.workspace_root()}),
    )?;
    assert!(as_bool(&ensure, "success")?);
    let ensured = lease.call_ok(ops::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    assert_eq!(
        as_i64(&ensured, "manifest_version")?,
        1,
        "a fresh ensured base should report the initial manifest version: {ensured}"
    );
    assert!(
        ensured["base_root_hash"]
            .as_str()
            .is_some_and(looks_like_sha256),
        "the ensured base binding should expose a CAS-shaped root hash: {ensured}"
    );
    Ok(())
}

#[test]
fn direct_file_ops_round_trip_through_protocol() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let path = "e2e/hello.txt";

    let write = lease.call_ok(
        ops::SANDBOX_FILE_WRITE,
        json!({"path": path, "content": "hello from protocol\n", "overwrite": true}),
    )?;
    assert!(as_bool(&write, "success")?);

    let read = lease.call_ok(ops::SANDBOX_FILE_READ, json!({"path": path}))?;
    assert_eq!(as_str(&read, "content")?, "hello from protocol\n");

    let edit = lease.call_ok(
        ops::SANDBOX_FILE_EDIT,
        json!({
            "path": path,
            "edits": [{"old_text": "hello", "new_text": "hi", "replace_all": false}]
        }),
    )?;
    assert!(as_bool(&edit, "success")?);

    let read = lease.call_ok(ops::SANDBOX_FILE_READ, json!({"path": path}))?;
    assert_eq!(as_str(&read, "content")?, "hi from protocol\n");
    Ok(())
}

#[test]
fn commit_to_workspace_survives_protocol_rebuild() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let path = "e2e/committed.txt";

    lease.call_ok(
        ops::SANDBOX_FILE_WRITE,
        json!({"path": path, "content": "committed through protocol\n", "overwrite": true}),
    )?;
    let commit = lease.call_ok(
        ops::SANDBOX_CHECKPOINT_COMMIT_TO_WORKSPACE,
        json!({"workspace_root": lease.workspace_root()}),
    )?;
    assert!(as_bool(&commit, "success")?);

    let rebuilt = lease.call_ok(
        ops::SANDBOX_CHECKPOINT_BUILD_BASE,
        json!({"workspace_root": lease.workspace_root(), "reset": true}),
    )?;
    assert!(as_bool(&rebuilt, "success")?);

    let read = lease.call_ok(ops::SANDBOX_FILE_READ, json!({"path": path}))?;
    assert_eq!(as_str(&read, "content")?, "committed through protocol\n");
    Ok(())
}
