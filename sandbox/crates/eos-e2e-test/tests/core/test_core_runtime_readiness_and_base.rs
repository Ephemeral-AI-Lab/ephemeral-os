use anyhow::Result;
use eos_operation::core::catalog;
use serde_json::{json, Value};

use crate::support::{as_bool, as_i64, envelope_meta, envelope_result, live_pool_or_skip};

#[test]
fn runtime_ready_handshake() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let ready_wire = lease.call(catalog::SANDBOX_RUNTIME_READY, json!({}))?;
    assert_eq!(
        ready_wire["status"], "ok",
        "runtime.ready uses ok envelope: {ready_wire}"
    );
    let ready = envelope_result(&ready_wire)?;
    assert!(as_bool(&ready, "ready")?, "daemon must be ready: {ready}");
    assert!(
        ready
            .get("probes")
            .and_then(Value::as_array)
            .is_some_and(|probes| !probes.is_empty()),
        "runtime.ready must include probe details: {ready}"
    );
    Ok(())
}

#[test]
fn ensure_base_creates_single_base_layer() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let metrics_wire = lease.call(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    assert_eq!(
        metrics_wire["status"], "ok",
        "layer metrics uses ok envelope: {metrics_wire}"
    );
    let metrics = envelope_result(&metrics_wire)?;
    assert_eq!(
        as_i64(&metrics, "manifest_depth")?,
        1,
        "fresh root should start at the base manifest: {metrics}"
    );
    assert_eq!(
        as_i64(&metrics, "referenced_layers")?,
        1,
        "fresh root should reference only the base layer: {metrics}"
    );
    Ok(())
}

#[test]
fn ensure_base_idempotent() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let before_wire = lease.call(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    let before = envelope_result(&before_wire)?;
    let ensure_wire = lease.call(
        catalog::SANDBOX_CHECKPOINT_ENSURE_BASE,
        json!({"workspace_root": lease.workspace_root()}),
    )?;
    assert_eq!(
        ensure_wire["status"], "ok",
        "ensure_base uses ok envelope: {ensure_wire}"
    );
    let ensure = envelope_result(&ensure_wire)?;
    assert!(
        !as_bool(&ensure, "created")?,
        "second ensure should not rebuild an existing base: {ensure}"
    );
    let after_wire = lease.call(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    let after = envelope_result(&after_wire)?;
    assert_eq!(
        as_i64(&after, "manifest_depth")?,
        as_i64(&before, "manifest_depth")?,
        "idempotent ensure must preserve depth: before={before} after={after}"
    );
    Ok(())
}

#[test]
fn build_base_reset_rebuilds() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "setup/reset.txt", "content": "before\n", "overwrite": true}),
    )?;
    let rebuilt_wire = lease.call(
        catalog::SANDBOX_CHECKPOINT_BUILD_BASE,
        json!({"workspace_root": lease.workspace_root(), "reset": true}),
    )?;
    assert_eq!(
        rebuilt_wire["status"], "ok",
        "build_base uses ok envelope: {rebuilt_wire}"
    );
    let rebuilt_meta = envelope_meta(&rebuilt_wire)?;
    let rebuilt = envelope_result(&rebuilt_wire)?;
    assert!(as_bool(&rebuilt, "success")?);
    assert!(
        rebuilt_meta
            .steps
            .iter()
            .any(|step| step.kind == "dispatch"),
        "build_base envelope should expose dispatch step meta: {rebuilt_wire}"
    );
    let metrics_wire = lease.call(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    let metrics = envelope_result(&metrics_wire)?;
    assert_eq!(
        as_i64(&metrics, "manifest_depth")?,
        1,
        "reset rebuild should collapse to a fresh base: {metrics}"
    );
    Ok(())
}

#[test]
fn workspace_binding_roundtrip() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let binding_wire = lease.call(catalog::SANDBOX_CHECKPOINT_BINDING, json!({}))?;
    assert_eq!(
        binding_wire["status"], "ok",
        "binding uses ok envelope: {binding_wire}"
    );
    let binding = envelope_result(&binding_wire)?;
    assert_eq!(
        binding["binding"]["workspace_root"],
        Value::String(lease.workspace_root().to_owned()),
        "workspace binding should round-trip the lease workspace root: {binding}"
    );
    Ok(())
}

#[test]
fn heartbeat_inflight_idle_zero() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
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
    let inflight_wire = lease.call(catalog::SANDBOX_CALL_COUNT, json!({}))?;
    assert_eq!(
        inflight_wire["status"], "ok",
        "inflight count uses ok envelope: {inflight_wire}"
    );
    let inflight = envelope_result(&inflight_wire)?;
    assert_eq!(
        as_i64(&inflight, "count")?,
        0,
        "idle lease should not have background invocations: {inflight}"
    );
    Ok(())
}
