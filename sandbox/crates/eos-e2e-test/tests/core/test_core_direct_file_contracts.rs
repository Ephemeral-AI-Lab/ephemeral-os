use std::sync::{Arc, Barrier};
use std::thread;

use anyhow::{Context, Result};
use eos_e2e_test::audit::section;
use eos_e2e_test::client::error_kind;
use eos_e2e_test::next_invocation_id;
use eos_protocol::{
    models::{MAX_FILE_BYTES, MAX_READ_BYTES},
    ops,
};
use serde_json::{json, Value};

use crate::support::{
    array, as_bool, as_i64, as_str, conflict_message, live_pool_or_skip, wait_for_active_leases,
};

/// Read a nested `timings.<key>` number from a response.
fn timing_f64(value: &Value, key: &str) -> Option<f64> {
    value
        .get("timings")
        .and_then(|timings| timings.get(key))
        .and_then(Value::as_f64)
}

#[test]
fn write_read_roundtrip() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "tool/roundtrip.txt", "content": "roundtrip\n", "overwrite": true}),
    )?;
    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": "tool/roundtrip.txt"}))?;
    assert!(as_bool(&read, "exists")?);
    assert_eq!(as_str(&read, "content")?, "roundtrip\n");
    assert_eq!(as_str(&read, "encoding")?, "utf-8");
    Ok(())
}

#[test]
fn write_publishes_changed_paths() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let write = lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "tool/changed.txt", "content": "changed\n", "overwrite": true}),
    )?;
    assert_eq!(as_str(&write, "status")?, "committed");
    assert_eq!(as_str(&write, "mutation_source")?, "api_write");
    assert!(as_bool(&write, "published")?);
    assert!(
        array(&write, "changed_paths")?
            .iter()
            .any(|path| path.as_str() == Some("tool/changed.txt")),
        "write response should list the published path: {write}"
    );
    Ok(())
}

#[test]
fn edit_search_replace_applied() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "tool/edit.txt", "content": "alpha beta\n", "overwrite": true}),
    )?;
    let edit = lease.call_ok(
        ops::API_V1_EDIT_FILE,
        json!({
            "path": "tool/edit.txt",
            "edits": [{"old_text": "alpha", "new_text": "omega", "replace_all": false}]
        }),
    )?;
    assert_eq!(as_i64(&edit, "applied_edits")?, 1);
    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": "tool/edit.txt"}))?;
    assert_eq!(as_str(&read, "content")?, "omega beta\n");
    Ok(())
}

#[test]
fn edit_replace_all() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "tool/replace-all.txt", "content": "x x x\n", "overwrite": true}),
    )?;
    lease.call_ok(
        ops::API_V1_EDIT_FILE,
        json!({
            "path": "tool/replace-all.txt",
            "edits": [{"old_text": "x", "new_text": "y", "replace_all": true}]
        }),
    )?;
    let read = lease.call_ok(
        ops::API_V1_READ_FILE,
        json!({"path": "tool/replace-all.txt"}),
    )?;
    assert_eq!(as_str(&read, "content")?, "y y y\n");
    Ok(())
}

#[test]
fn edit_anchor_not_found() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "tool/not-found.txt", "content": "present\n", "overwrite": true}),
    )?;
    let edit = lease.call(
        ops::API_V1_EDIT_FILE,
        json!({
            "path": "tool/not-found.txt",
            "edits": [{"old_text": "absent", "new_text": "x", "replace_all": false}]
        }),
    )?;
    assert!(
        conflict_message(&edit).contains("anchor not found"),
        "missing anchor should surface the edit error catalog: {edit}"
    );
    Ok(())
}

#[test]
fn edit_count_mismatch() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "tool/count-mismatch.txt", "content": "dup dup\n", "overwrite": true}),
    )?;
    let edit = lease.call(
        ops::API_V1_EDIT_FILE,
        json!({
            "path": "tool/count-mismatch.txt",
            "edits": [{"old_text": "dup", "new_text": "x", "replace_all": false}]
        }),
    )?;
    assert!(
        conflict_message(&edit).contains("count mismatch"),
        "ambiguous anchor should surface the edit error catalog: {edit}"
    );
    Ok(())
}

#[test]
fn read_nonexistent() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": "tool/missing.txt"}))?;
    assert!(!as_bool(&read, "exists")?);
    assert_eq!(as_str(&read, "content")?, "");
    Ok(())
}

#[test]
fn read_max_bytes_guard() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!("mkdir -p tool && python3 - <<'PY'\nopen('tool/too-big-read.txt', 'wb').write(b'x' * {})\nPY", MAX_READ_BYTES + 1),
            "yield_time_ms": 1000,
            "timeout_seconds": 20,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(
        as_str(&exec, "status")?,
        "ok",
        "seed command should publish big file: {exec}"
    );
    let read = lease.call(
        ops::API_V1_READ_FILE,
        json!({"path": "tool/too-big-read.txt"}),
    )?;
    assert_eq!(error_kind(&read), Some("invalid_envelope"));
    assert!(
        read.get("error")
            .and_then(|error| error.get("message"))
            .and_then(Value::as_str)
            .context("error message")?
            .contains("file too large"),
        "large read should fail with the read guard: {read}"
    );
    Ok(())
}

#[test]
fn fast_path_write_edit_emit_no_overlay_or_lease_audit() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let mut audit = lease.audit_tap()?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "fastpath/no-overlay.txt", "content": "x\n", "overwrite": true}),
    )?;
    lease.call_ok(
        ops::API_V1_EDIT_FILE,
        json!({
            "path": "fastpath/no-overlay.txt",
            "edits": [{"old_text": "x", "new_text": "y", "replace_all": false}]
        }),
    )?;
    audit.collect()?;
    // The fast path bypasses the overlay pipeline entirely: no overlay mount is
    // built and no lease is released, so the overlay/lease lifecycle events that a
    // foreground exec emits do NOT fire — but the OCC publish still does. (Only
    // events that the paired positive-contrast test confirms are emitted are
    // asserted negatively here; `lease_acquired` is gated on a timing the exec
    // path never produces, so a negative on it would be vacuous.)
    assert!(
        !audit.any("layer_stack.lease_released"),
        "fast-path write/edit must not release a layer lease: {:?}",
        audit.events()
    );
    assert!(
        !audit.any("overlay_workspace.cleanup"),
        "fast-path write/edit must not build/recycle an overlay: {:?}",
        audit.events()
    );
    assert!(
        audit.any("occ.publish"),
        "fast-path write must still publish through OCC: {:?}",
        audit.events()
    );
    Ok(())
}

#[test]
fn foreground_exec_emits_lease_and_overlay_audit() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let mut audit = lease.audit_tap()?;
    // A foreground exec (completes within the yield, so NO command_session_id)
    // DOES run the overlay pipeline: lease acquire/release + overlay cleanup.
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "mkdir -p fastpath && printf hi > fastpath/exec.txt",
            "yield_time_ms": 1000,
            "timeout_seconds": 20,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&exec, "status")?, "ok", "exec must complete: {exec}");
    assert!(
        exec.get("command_session_id").is_none(),
        "completed foreground exec must not be a background session: {exec}"
    );
    audit.collect()?;
    assert!(
        audit.any("layer_stack.lease_released"),
        "foreground exec must release a layer lease: {:?}",
        audit.events()
    );
    let cleanup = audit
        .first("overlay_workspace.cleanup")
        .context("foreground exec must emit overlay_workspace.cleanup")?;
    assert_eq!(
        section(cleanup, "overlay_workspace")
            .and_then(|overlay| overlay.get("workspace_mode"))
            .and_then(Value::as_str),
        Some("ephemeral"),
        "overlay cleanup should report ephemeral mode: {cleanup}"
    );
    Ok(())
}

#[test]
fn fast_path_surfaces_occ_and_read_timings() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let write = lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "fastpath/timings.txt", "content": "t\n", "overwrite": true}),
    )?;
    // The fast path accounts its work as a direct LayerStack/OCC operation
    // (occ_apply / layer_stack_read), not via overlay-capture timing keys.
    assert!(
        timing_f64(&write, "api.write.occ_apply_s").is_some(),
        "fast-path write should surface api.write.occ_apply_s: {write}"
    );
    let read = lease.call_ok(
        ops::API_V1_READ_FILE,
        json!({"path": "fastpath/timings.txt"}),
    )?;
    assert!(
        timing_f64(&read, "api.read.layer_stack_read_s").is_some(),
        "fast-path read should surface api.read.layer_stack_read_s: {read}"
    );
    Ok(())
}

#[test]
fn repeated_fast_path_writes_keep_leases_zero() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    for index in 0..30 {
        lease.call_ok(
            ops::API_V1_WRITE_FILE,
            json!({"path": "fastpath/leak.txt", "content": format!("v{index}\n"), "overwrite": true}),
        )?;
    }
    // The fast path never leases: across 30 overwrites no overlay lease is ever
    // taken, so both the live lease count and the held-layer count stay at 0
    // (the no-lease-leak half of the spec-point-4 "fast path bypasses overlay").
    let metrics = wait_for_active_leases(&lease, 0)?;
    assert_eq!(
        as_i64(&metrics, "leased_layers")?,
        0,
        "fast-path writes must hold no leased layers: {metrics}"
    );
    Ok(())
}

#[test]
fn direct_file_ops_concurrency_ladder() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let levels = pool.workload().concurrency_levels.clone();
    let lease = pool.acquire()?;
    for level in levels {
        let barrier = Arc::new(Barrier::new(level));
        let handles: Vec<_> = (0..level)
            .map(|index| {
                let client = lease.client().clone();
                let root = lease.root().to_owned();
                let caller_id = lease.caller_id().to_owned();
                let barrier = Arc::clone(&barrier);
                thread::spawn(move || {
                    barrier.wait();
                    client.request(
                        ops::API_V1_WRITE_FILE,
                        &next_invocation_id(),
                        &json!({
                            "layer_stack_root": root,
                            "caller_id": caller_id,
                            "path": format!("fastpath/ladder-{level}-{index}.txt"),
                            "content": format!("level={level} index={index}\n"),
                            "overwrite": true
                        }),
                    )
                })
            })
            .collect();

        for handle in handles {
            let response = handle.join().expect("direct write thread panicked")?;
            assert!(
                as_bool(&response, "success")?,
                "direct write ladder level {level} should commit: {response}"
            );
            assert_eq!(as_str(&response, "status")?, "committed");
            assert!(
                timing_f64(&response, "api.write.occ_apply_s").is_some(),
                "direct write ladder should surface OCC timing: {response}"
            );
        }
        for index in 0..level {
            let read = lease.call_ok(
                ops::API_V1_READ_FILE,
                json!({"path": format!("fastpath/ladder-{level}-{index}.txt")}),
            )?;
            assert_eq!(
                as_str(&read, "content")?,
                format!("level={level} index={index}\n")
            );
        }
        let metrics = wait_for_active_leases(&lease, 0)?;
        assert_eq!(
            as_i64(&metrics, "leased_layers")?,
            0,
            "direct file ladder must not hold leased layers after level {level}: {metrics}"
        );
    }
    Ok(())
}

#[test]
fn write_max_file_bytes_guard() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let write = lease.call(
        ops::API_V1_WRITE_FILE,
        json!({
            "path": "tool/too-big-write.txt",
            "content": "x".repeat(MAX_FILE_BYTES + 1),
            "overwrite": true
        }),
    )?;
    assert_eq!(error_kind(&write), Some("invalid_envelope"));
    assert!(
        write
            .get("error")
            .and_then(|error| error.get("message"))
            .and_then(Value::as_str)
            .context("error message")?
            .contains("file too large"),
        "large write should fail before OCC publish: {write}"
    );
    Ok(())
}

#[test]
fn write_above_legacy_two_mib_cap_succeeds() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // 3 MiB sits above the legacy 2 MiB write cap and below the configured
    // 8 MiB cap (`daemon.files.max_write_bytes`). Proves the raised, config
    // driven write limit is honored end to end, not just the old hardcoded 2 MiB.
    let size = 3 * 1024 * 1024;
    let write = lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "tool/three-mib.txt", "content": "x".repeat(size), "overwrite": true}),
    )?;
    assert!(
        as_bool(&write, "success")?,
        "3 MiB write should publish under the raised cap: {write}"
    );
    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": "tool/three-mib.txt"}))?;
    assert_eq!(
        as_str(&read, "content")?.len(),
        size,
        "3 MiB readback should match the written length"
    );
    Ok(())
}
