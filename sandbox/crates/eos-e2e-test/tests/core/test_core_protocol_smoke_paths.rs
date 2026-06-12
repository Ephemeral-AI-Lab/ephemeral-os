use anyhow::Result;
use eos_e2e_test::cas::looks_like_sha256;
use eos_operation::core::catalog;
use serde_json::{json, Value};

use crate::support::{as_bool, as_i64, as_str, envelope_meta, envelope_result, live_pool_or_skip};

#[test]
fn setup_readiness_and_metrics_are_protocol_visible() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;

    let ready_wire = lease.call(catalog::SANDBOX_RUNTIME_READY, json!({}))?;
    let ready_meta = envelope_meta(&ready_wire)?;
    assert_eq!(
        ready_wire["status"], "ok",
        "runtime.ready uses ok envelope: {ready_wire}"
    );
    assert_eq!(ready_meta.op, catalog::SANDBOX_RUNTIME_READY);
    let ready = envelope_result(&ready_wire)?;
    assert!(as_bool(&ready, "ready")?);

    let heartbeat_wire = lease.call(
        catalog::SANDBOX_CALL_HEARTBEAT,
        json!({"invocation_ids": []}),
    )?;
    assert_eq!(
        heartbeat_wire["status"], "ok",
        "heartbeat uses ok envelope: {heartbeat_wire}"
    );
    let heartbeat = envelope_result(&heartbeat_wire)?;
    assert!(as_bool(&heartbeat, "success")?);

    let binding_wire = lease.call(catalog::SANDBOX_CHECKPOINT_BINDING, json!({}))?;
    assert_eq!(
        binding_wire["status"], "ok",
        "binding uses ok envelope: {binding_wire}"
    );
    let binding = envelope_result(&binding_wire)?;
    assert_eq!(
        binding["binding"]["workspace_root"],
        Value::String(lease.workspace_root().to_owned())
    );

    let metrics_wire = lease.call(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    assert_eq!(
        metrics_wire["status"], "ok",
        "layer metrics uses ok envelope: {metrics_wire}"
    );
    let metrics = envelope_result(&metrics_wire)?;
    assert!(as_bool(&metrics, "workspace_bound")?);
    assert_eq!(as_i64(&metrics, "active_leases")?, 0);

    let ensure_wire = lease.call(
        catalog::SANDBOX_CHECKPOINT_ENSURE_BASE,
        json!({"workspace_root": lease.workspace_root()}),
    )?;
    assert_eq!(
        ensure_wire["status"], "ok",
        "ensure_base uses ok envelope: {ensure_wire}"
    );
    let ensure = envelope_result(&ensure_wire)?;
    assert!(as_bool(&ensure, "success")?);
    let ensured = lease.call_ok(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
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

    let write = lease.call(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": path, "content": "hello from protocol\n", "overwrite": true}),
    )?;
    assert_eq!(
        write["status"], "ok",
        "file write uses ok envelope: {write}"
    );
    let write = envelope_result(&write)?;
    assert_eq!(as_str(write, "status")?, "committed");

    let read_wire = lease.call(catalog::SANDBOX_FILE_READ, json!({"path": path}))?;
    assert_eq!(
        read_wire["status"], "ok",
        "file read uses ok envelope: {read_wire}"
    );
    let read = envelope_result(&read_wire)?;
    assert_eq!(as_str(read, "content")?, "hello from protocol\n");

    let edit = lease.call(
        catalog::SANDBOX_FILE_EDIT,
        json!({
            "path": path,
            "edits": [{"old_text": "hello", "new_text": "hi", "replace_all": false}]
        }),
    )?;
    assert_eq!(edit["status"], "ok", "file edit uses ok envelope: {edit}");
    let edit = envelope_result(&edit)?;
    assert_eq!(as_str(edit, "status")?, "committed");

    let read_wire = lease.call(catalog::SANDBOX_FILE_READ, json!({"path": path}))?;
    assert_eq!(
        read_wire["status"], "ok",
        "file read after edit uses ok envelope: {read_wire}"
    );
    let read = envelope_result(&read_wire)?;
    assert_eq!(as_str(read, "content")?, "hi from protocol\n");
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
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": path, "content": "committed through protocol\n", "overwrite": true}),
    )?;
    let commit_wire = lease.call(
        catalog::SANDBOX_CHECKPOINT_COMMIT_TO_WORKSPACE,
        json!({"workspace_root": lease.workspace_root()}),
    )?;
    assert_eq!(
        commit_wire["status"], "ok",
        "commit_to_workspace uses ok envelope: {commit_wire}"
    );
    let commit = envelope_result(&commit_wire)?;
    assert!(as_bool(&commit, "success")?);

    let rebuilt_wire = lease.call(
        catalog::SANDBOX_CHECKPOINT_BUILD_BASE,
        json!({"workspace_root": lease.workspace_root(), "reset": true}),
    )?;
    assert_eq!(
        rebuilt_wire["status"], "ok",
        "build_base uses ok envelope: {rebuilt_wire}"
    );
    let rebuilt = envelope_result(&rebuilt_wire)?;
    assert!(as_bool(&rebuilt, "success")?);

    let read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": path}))?;
    assert_eq!(as_str(&read, "content")?, "committed through protocol\n");
    Ok(())
}
