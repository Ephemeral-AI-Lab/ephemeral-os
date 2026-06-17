use std::sync::{Arc, Barrier};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use config::configs::daemon::MAX_FILE_BYTES;
use e2e_test::next_invocation_id;
use protocol::catalog;
use serde_json::{json, Value};

use crate::support::{
    array, as_bool, as_i64, as_str, conflict_message, envelope_meta, envelope_result,
    finalize_foreground_command, has_trace_event, live_pool_or_skip, trace_record,
    wait_for_active_leases,
};

const CORE_MAX_READ_BYTES: usize = 4 * 1024 * 1024;

#[test]
fn write_read_roundtrip() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "tool/roundtrip.txt", "content": "roundtrip\n", "overwrite": true}),
    )?;
    let read = lease.call_ok(
        catalog::SANDBOX_FILE_READ,
        json!({"path": "tool/roundtrip.txt"}),
    )?;
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
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "tool/changed.txt", "content": "changed\n", "overwrite": true}),
    )?;
    assert_eq!(as_str(&write, "status")?, "committed");
    assert_eq!(as_str(&write, "mutation_source")?, "direct_write");
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
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "tool/edit.txt", "content": "alpha beta\n", "overwrite": true}),
    )?;
    let edit = lease.call_ok(
        catalog::SANDBOX_FILE_EDIT,
        json!({
            "path": "tool/edit.txt",
            "edits": [{"old_text": "alpha", "new_text": "omega", "replace_all": false}]
        }),
    )?;
    assert_eq!(as_i64(&edit, "applied_edits")?, 1);
    let read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": "tool/edit.txt"}))?;
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
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "tool/replace-all.txt", "content": "x x x\n", "overwrite": true}),
    )?;
    lease.call_ok(
        catalog::SANDBOX_FILE_EDIT,
        json!({
            "path": "tool/replace-all.txt",
            "edits": [{"old_text": "x", "new_text": "y", "replace_all": true}]
        }),
    )?;
    let read = lease.call_ok(
        catalog::SANDBOX_FILE_READ,
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
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "tool/not-found.txt", "content": "present\n", "overwrite": true}),
    )?;
    let edit = lease.call(
        catalog::SANDBOX_FILE_EDIT,
        json!({
            "path": "tool/not-found.txt",
            "edits": [{"old_text": "absent", "new_text": "x", "replace_all": false}]
        }),
    )?;
    let edit = envelope_result(&edit)?;
    assert!(
        conflict_message(edit).contains("anchor not found"),
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
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "tool/count-mismatch.txt", "content": "dup dup\n", "overwrite": true}),
    )?;
    let edit = lease.call(
        catalog::SANDBOX_FILE_EDIT,
        json!({
            "path": "tool/count-mismatch.txt",
            "edits": [{"old_text": "dup", "new_text": "x", "replace_all": false}]
        }),
    )?;
    let edit = envelope_result(&edit)?;
    assert!(
        conflict_message(edit).contains("count mismatch"),
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
    let read = lease.call_ok(
        catalog::SANDBOX_FILE_READ,
        json!({"path": "tool/missing.txt"}),
    )?;
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
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": format!("mkdir -p tool && python3 - <<'PY'\nopen('tool/too-big-read.txt', 'wb').write(b'x' * {})\nPY", CORE_MAX_READ_BYTES + 1),
            "yield_time_ms": 1000,
            "timeout_seconds": 20,}),
    )?;
    let exec = finalize_foreground_command(&lease, exec, Instant::now() + Duration::from_secs(30))?;
    assert_eq!(
        as_str(&exec, "status")?,
        "ok",
        "seed command should publish big file: {exec}"
    );
    let read = lease.call(
        catalog::SANDBOX_FILE_READ,
        json!({"path": "tool/too-big-read.txt"}),
    )?;
    let error = error_fault(&read)?;
    assert_eq!(error["kind"], "invalid_request", "{read}");
    assert!(
        error
            .get("message")
            .and_then(Value::as_str)
            .context("error message")?
            .contains("file too large"),
        "large read should fail with the read guard: {read}"
    );
    Ok(())
}

#[test]
fn fast_path_write_publishes_without_holding_a_lease() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let before = lease.call_ok(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    lease.call_ok(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "fastpath/no-overlay.txt", "content": "x\n", "overwrite": true}),
    )?;
    lease.call_ok(
        catalog::SANDBOX_FILE_EDIT,
        json!({
            "path": "fastpath/no-overlay.txt",
            "edits": [{"old_text": "x", "new_text": "y", "replace_all": false}]
        }),
    )?;
    // The fast path bypasses the overlay pipeline entirely: writes commit
    // directly through OCC (manifest version advances) without ever taking a
    // snapshot lease (active_leases stays flat).
    let after = lease.call_ok(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    assert!(
        as_i64(&after, "manifest_version")? > as_i64(&before, "manifest_version")?,
        "fast-path write/edit must publish through OCC: before={before} after={after}"
    );
    assert_eq!(
        as_i64(&after, "active_leases")?,
        as_i64(&before, "active_leases")?,
        "fast-path write/edit must not hold a layer lease: {after}"
    );
    Ok(())
}

#[test]
fn fast_path_records_occ_and_read_trace_events() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let write = lease.call(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "fastpath/trace-events.txt", "content": "t\n", "overwrite": true}),
    )?;
    assert_eq!(as_str(&write, "status")?, "ok", "{write}");
    let write_result = envelope_result(&write)?;
    assert_eq!(as_str(write_result, "status")?, "committed");
    let write_record = trace_record(&write)?;
    assert!(
        has_trace_event(&write_record, "occ", "commit_finished", |details| {
            details["success"] == true
                && details["published_file_count"]
                    .as_i64()
                    .is_some_and(|count| count >= 1)
        }),
        "fast-path write should record OCC commit facts: {write_record:?}"
    );
    assert!(
        has_trace_event(&write_record, "file", "write_applied", |details| {
            details["changed_paths"]
                .as_array()
                .is_some_and(|paths| paths.iter().any(|path| path == "fastpath/trace-events.txt"))
        }),
        "fast-path write should record file write facts: {write_record:?}"
    );

    let read = lease.call(
        catalog::SANDBOX_FILE_READ,
        json!({"path": "fastpath/trace-events.txt"}),
    )?;
    assert_eq!(as_str(&read, "status")?, "ok", "{read}");
    let read_result = envelope_result(&read)?;
    assert_eq!(as_str(read_result, "content")?, "t\n");
    let read_record = trace_record(&read)?;
    assert!(
        has_trace_event(&read_record, "file", "read_finished", |details| {
            details["success"] == true
                && details["exists"] == true
                && details["workspace"] == "host"
        }),
        "fast-path read should record file read facts: {read_record:?}"
    );
    Ok(())
}

#[test]
fn live_trace_file_fast_path_records_route_occ_and_no_workspace_facts() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let write = lease.call(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "fastpath/trace.txt", "content": "trace\n", "overwrite": true}),
    )?;
    assert_eq!(as_str(&write, "status")?, "ok", "{write}");
    let meta = envelope_meta(&write)?;
    assert_eq!(meta.op, catalog::SANDBOX_FILE_WRITE);
    assert_eq!(meta.workspace_route.kind, trace::WorkspaceRoute::FastPath);
    let record = trace_record(&write)?;

    assert!(
        has_trace_event(&record, "workspace.route", "route_selected", |details| {
            details["kind"] == "fast_path" && details["reason"] == "no_isolated_network_for_caller"
        }),
        "fast-path file write should record direct no-workspace route facts: {record:?}"
    );
    assert!(
        has_trace_event(&record, "occ", "commit_finished", |details| {
            details["success"] == true
                && details["published_file_count"]
                    .as_i64()
                    .is_some_and(|count| count >= 1)
        }),
        "fast-path file write should record OCC commit facts: {record:?}"
    );
    assert!(
        has_trace_event(&record, "file", "write_applied", |details| {
            details["workspace"] == "host"
                && details["published"] == true
                && details["changed_paths"]
                    .as_array()
                    .is_some_and(|paths| paths.iter().any(|path| path == "fastpath/trace.txt"))
        }),
        "fast-path file write should record file publish facts: {record:?}"
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
            catalog::SANDBOX_FILE_WRITE,
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
                let client = lease.recorded_client();
                let root = lease.root().to_owned();
                let caller_id = lease.caller_id().to_owned();
                let barrier = Arc::clone(&barrier);
                thread::spawn(move || {
                    barrier.wait();
                    client.request(
                        catalog::SANDBOX_FILE_WRITE,
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

        for (index, handle) in handles.into_iter().enumerate() {
            let response = handle.join().expect("direct write thread panicked")?;
            assert_eq!(
                as_str(&response, "status")?,
                "ok",
                "direct write ladder level {level} should commit: {response}"
            );
            let result = envelope_result(&response)?;
            assert_eq!(as_str(result, "status")?, "committed");
            let expected_path = format!("fastpath/ladder-{level}-{index}.txt");
            assert!(
                array(result, "changed_paths")?
                    .iter()
                    .any(|path| path.as_str() == Some(expected_path.as_str())),
                "direct write ladder should report changed path: {response}"
            );
        }
        for index in 0..level {
            let read = lease.call_ok(
                catalog::SANDBOX_FILE_READ,
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
        catalog::SANDBOX_FILE_WRITE,
        json!({
            "path": "tool/too-big-write.txt",
            "content": "x".repeat(MAX_FILE_BYTES + 1),
            "overwrite": true
        }),
    )?;
    let error = error_fault(&write)?;
    assert_eq!(error["kind"], "invalid_request", "{write}");
    assert!(
        error
            .get("message")
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
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "tool/three-mib.txt", "content": "x".repeat(size), "overwrite": true}),
    )?;
    assert_eq!(as_str(&write, "status")?, "committed");
    let read = lease.call_ok(
        catalog::SANDBOX_FILE_READ,
        json!({"path": "tool/three-mib.txt"}),
    )?;
    assert_eq!(
        as_str(&read, "content")?.len(),
        size,
        "3 MiB readback should match the written length"
    );
    Ok(())
}

fn error_fault(response: &Value) -> Result<&Value> {
    assert_eq!(as_str(response, "status")?, "error", "{response}");
    response
        .get("error")
        .context("error envelope should include fault")
}
