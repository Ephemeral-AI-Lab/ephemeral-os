use anyhow::{Context, Result};
use eos_e2e_test::audit::section;
use eos_e2e_test::cas::looks_like_sha256;
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{as_bool, as_i64, as_str, live_pool_or_skip};

#[test]
fn setup_readiness_metrics_and_audit_are_protocol_visible() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;

    let ready = lease.call_ok(ops::API_RUNTIME_READY, json!({}))?;
    assert!(as_bool(&ready, "success")?);
    assert!(as_bool(&ready, "ready")?);

    let heartbeat = lease.call_ok(ops::API_V1_HEARTBEAT, json!({"invocation_ids": []}))?;
    assert!(as_bool(&heartbeat, "success")?);

    let binding = lease.call_ok(ops::API_WORKSPACE_BINDING, json!({}))?;
    assert_eq!(
        binding["binding"]["workspace_root"],
        Value::String(lease.workspace_root().to_owned())
    );

    let metrics = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
    assert!(as_bool(&metrics, "workspace_bound")?);
    assert_eq!(as_i64(&metrics, "active_leases")?, 0);

    let snapshot = lease.call_ok(ops::API_AUDIT_SNAPSHOT, json!({}))?;
    assert!(as_bool(&snapshot, "success")?);

    let mut audit = lease.audit_tap()?;
    let ensure = lease.call_ok(
        ops::API_ENSURE_WORKSPACE_BASE,
        json!({"workspace_root": lease.workspace_root()}),
    )?;
    assert!(as_bool(&ensure, "success")?);
    audit.collect()?;
    if let Some(event) = audit.first("workspace_base.ensured") {
        let layer_stack = section(event, "layer_stack").context("layer_stack audit section")?;
        assert_eq!(
            layer_stack.get("manifest_version").and_then(Value::as_i64),
            Some(1),
            "workspace_base audit should include the active manifest version: {event}"
        );
        assert!(
            layer_stack
                .get("manifest_root_hash")
                .and_then(Value::as_str)
                .is_some_and(looks_like_sha256),
            "workspace_base audit should include a CAS-shaped manifest hash: {event}"
        );
    }
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
        ops::API_V1_WRITE_FILE,
        json!({"path": path, "content": "hello from protocol\n", "overwrite": true}),
    )?;
    assert!(as_bool(&write, "success")?);

    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": path}))?;
    assert_eq!(as_str(&read, "content")?, "hello from protocol\n");

    let edit = lease.call_ok(
        ops::API_V1_EDIT_FILE,
        json!({
            "path": path,
            "edits": [{"old_text": "hello", "new_text": "hi", "replace_all": false}]
        }),
    )?;
    assert!(as_bool(&edit, "success")?);

    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": path}))?;
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
        ops::API_V1_WRITE_FILE,
        json!({"path": path, "content": "committed through protocol\n", "overwrite": true}),
    )?;
    let mut audit = lease.audit_tap()?;
    let commit = lease.call_ok(
        ops::API_COMMIT_TO_WORKSPACE,
        json!({"workspace_root": lease.workspace_root()}),
    )?;
    assert!(as_bool(&commit, "success")?);
    audit.collect()?;
    if let Some(event) = audit.first("layer_stack.commit_completed") {
        let layer_stack = section(event, "layer_stack").context("layer_stack audit section")?;
        assert_eq!(
            layer_stack.get("manifest_version").and_then(Value::as_i64),
            commit.get("manifest_version").and_then(Value::as_i64),
            "commit audit manifest_version should match response: {event}"
        );
        assert!(
            layer_stack
                .get("manifest_root_hash")
                .and_then(Value::as_str)
                .is_some_and(looks_like_sha256),
            "commit audit should include a CAS-shaped manifest hash: {event}"
        );
    }

    let rebuilt = lease.call_ok(
        ops::API_BUILD_WORKSPACE_BASE,
        json!({"workspace_root": lease.workspace_root(), "reset": true}),
    )?;
    assert!(as_bool(&rebuilt, "success")?);

    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": path}))?;
    assert_eq!(as_str(&read, "content")?, "committed through protocol\n");
    Ok(())
}
