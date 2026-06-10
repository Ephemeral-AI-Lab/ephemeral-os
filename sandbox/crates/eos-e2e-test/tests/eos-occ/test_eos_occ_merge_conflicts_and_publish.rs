use std::sync::{Arc, Barrier};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use eos_e2e_test::{next_invocation_id, unique_suffix};
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{
    array, as_bool, as_i64, as_str, conflict_reason, live_pool_or_skip,
    wait_for_command_stdout_contains, wait_for_session_count,
};

#[test]
fn concurrent_conflicting_writes() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let barrier = Arc::new(Barrier::new(2));
    let handles: Vec<_> = ["left", "right"]
        .into_iter()
        .map(|label| {
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
                        "path": "occ/conflict.txt",
                        "content": format!("{label}\n"),
                        "overwrite": true
                    }),
                )
            })
        })
        .collect();
    let responses: Vec<Value> = handles
        .into_iter()
        .map(|handle| Ok(handle.join().expect("writer thread panicked")?))
        .collect::<Result<_>>()?;
    assert!(
        responses
            .iter()
            .any(|response| response.get("status").and_then(Value::as_str) == Some("committed")),
        "at least one writer should publish: {responses:?}"
    );
    for response in &responses {
        assert!(
            as_bool(response, "success").unwrap_or(false) || response.get("conflict").is_some(),
            "write should either commit or surface a conflict: {response}"
        );
    }
    let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": "occ/conflict.txt"}))?;
    assert!(
        matches!(as_str(&read, "content")?, "left\n" | "right\n"),
        "final content should be one coherent writer output: {read}"
    );
    Ok(())
}

#[test]
fn concurrent_disjoint_writes() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let barrier = Arc::new(Barrier::new(6));
    let handles: Vec<_> = (0..6)
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
                        "path": format!("occ/disjoint-{index}.txt"),
                        "content": format!("{index}\n"),
                        "overwrite": true
                    }),
                )
            })
        })
        .collect();
    for handle in handles {
        let response = handle.join().expect("writer thread panicked")?;
        assert!(
            as_bool(&response, "success")?,
            "disjoint write should commit: {response}"
        );
    }
    for index in 0..6 {
        let read = lease.call_ok(
            ops::API_V1_READ_FILE,
            json!({"path": format!("occ/disjoint-{index}.txt")}),
        )?;
        assert_eq!(as_str(&read, "content")?, format!("{index}\n"));
    }
    Ok(())
}

#[test]
fn edit_overlap_conflict() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "occ/overlap.txt", "content": "dup dup\n", "overwrite": true}),
    )?;
    let edit = lease.call(
        ops::API_V1_EDIT_FILE,
        json!({
            "path": "occ/overlap.txt",
            "edits": [{"old_text": "dup", "new_text": "x", "replace_all": false}]
        }),
    )?;
    assert_eq!(
        conflict_reason(&edit),
        "aborted_overlap",
        "overlap conflict expected: {edit}"
    );
    Ok(())
}

#[test]
fn edit_anchor_errors_do_not_publish_or_advance_manifest() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let path = format!("occ/edit-anchor-{}.txt", unique_suffix());
    let original = "alpha beta alpha\n";
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": path, "content": original, "overwrite": true}),
    )?;
    let baseline = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
    let baseline_depth = as_i64(&baseline, "manifest_depth")?;

    for old_text in ["missing", "alpha"] {
        let edit = lease.call(
            ops::API_V1_EDIT_FILE,
            json!({
                "path": path,
                "edits": [{"old_text": old_text, "new_text": "changed", "replace_all": false}]
            }),
        )?;
        assert!(!as_bool(&edit, "success")?, "{edit}");
        assert_eq!(as_str(&edit, "status")?, "aborted_overlap", "{edit}");
        assert_eq!(conflict_reason(&edit), "aborted_overlap", "{edit}");
        assert_eq!(as_i64(&edit, "applied_edits")?, 0, "{edit}");
        assert!(
            array(&edit, "changed_paths")?.is_empty(),
            "anchor conflict must not publish changed paths: {edit}"
        );

        let read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": path}))?;
        assert_eq!(
            as_str(&read, "content")?,
            original,
            "anchor conflict must leave content unchanged: {read}"
        );
        let metrics = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
        assert_eq!(
            as_i64(&metrics, "manifest_depth")?,
            baseline_depth,
            "anchor conflict must not advance manifest-visible depth: {metrics}"
        );
    }
    Ok(())
}

#[test]
fn retry_budget_3x_surfaces_coherent_result() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let barrier = Arc::new(Barrier::new(12));
    let handles: Vec<_> = (0..12)
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
                        "path": "occ/retry-budget.txt",
                        "content": format!("{index}\n"),
                        "overwrite": true
                    }),
                )
            })
        })
        .collect();
    for handle in handles {
        let response = handle.join().expect("writer thread panicked")?;
        assert!(
            response.get("status").is_some() || response.get("error").is_some(),
            "concurrent writer should return a structured protocol payload: {response}"
        );
    }
    let read = lease.call_ok(
        ops::API_V1_READ_FILE,
        json!({"path": "occ/retry-budget.txt"}),
    )?;
    assert!(
        as_str(&read, "content")?.trim().parse::<usize>().is_ok(),
        "final content should be one whole writer output: {read}"
    );
    Ok(())
}

#[test]
fn atomic_overlay_changeset_drops_all_paths_on_stale_conflict() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let dir = format!("occ-atomic-{}", unique_suffix());
    let conflicted = format!("{dir}/conflicted.txt");
    let sibling = format!("{dir}/sibling.txt");
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": &conflicted, "content": "base\n", "overwrite": true}),
    )?;

    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!("bash -lc 'printf SNAPSHOT_READY; sleep 2; mkdir -p {dir}; printf stale > {conflicted}; printf sibling > {sibling}'"),
            "yield_time_ms": 500,
            "timeout_seconds": 30,}),
    )?;
    assert_eq!(as_str(&exec, "status")?, "running", "{exec}");
    let session_id = as_str(&exec, "command_session_id")?.to_owned();
    // The marker can slip past the 500ms yield window under emulation (a slow
    // boot-to-dispatch leaves stdout uncaptured at yield), so poll read_progress
    // until it surfaces instead of asserting on the yielded snapshot. This also
    // pins the snapshot-taken sync point before the concurrent direct write below.
    wait_for_command_stdout_contains(&lease, &session_id, "SNAPSHOT_READY")?;

    let body = (|| -> Result<()> {
        lease.call_ok(
            ops::API_V1_WRITE_FILE,
            json!({"path": &conflicted, "content": "newer\n", "overwrite": true}),
        )?;
        let result = wait_for_completion(&lease, &session_id)?;
        assert_eq!(
            as_str(&result, "status")?,
            "ok",
            "the command process itself should finish normally: {result}"
        );
        assert!(
            !as_bool(&result, "success")?,
            "stale changeset must not report a successful workspace mutation: {result}"
        );
        assert_eq!(
            conflict_reason(&result),
            "aborted_version",
            "stale conflict should reject the atomic changeset: {result}"
        );
        assert!(
            array(&result, "changed_paths")?.is_empty(),
            "atomic stale conflict must not publish any changed paths: {result}"
        );
        wait_for_session_count(&lease, 0)?;

        let conflicted_read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": &conflicted}))?;
        assert_eq!(
            as_str(&conflicted_read, "content")?,
            "newer\n",
            "newer direct content must win: {conflicted_read}"
        );
        let sibling_read = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": &sibling}))?;
        assert!(
            !as_bool(&sibling_read, "exists")?,
            "non-conflicting sibling from the same atomic changeset must not publish: {sibling_read}"
        );
        Ok(())
    })();

    if body.is_err() {
        let _ = lease.call(
            ops::API_V1_COMMAND_CANCEL,
            json!({"command_session_id": session_id}),
        );
        let _ = wait_for_session_count(&lease, 0);
    }
    body
}

#[test]
fn publish_accounting() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let mut audit = lease.audit_tap()?;
    let write = lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "occ/audit.txt", "content": "audit\n", "overwrite": true}),
    )?;
    assert!(!array(&write, "changed_paths")?.is_empty());
    audit.collect()?;
    assert!(
        audit.any("occ.publish"),
        "write publish should emit occ.publish: {:?}",
        audit.events()
    );
    Ok(())
}

fn wait_for_completion(lease: &eos_e2e_test::NodeLease<'_>, session_id: &str) -> Result<Value> {
    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        let collected = lease.call_ok(
            ops::API_V1_COMMAND_COLLECT_COMPLETED,
            json!({"command_session_ids": [session_id]}),
        )?;
        if let Some(completion) = array(&collected, "completions")?.first() {
            return completion
                .get("result")
                .cloned()
                .context("completion missing result");
        }
        if Instant::now() >= deadline {
            bail!("session {session_id} never completed");
        }
        thread::sleep(Duration::from_millis(100));
    }
}

#[test]
fn route_fileresult_catalog() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let committed = lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "occ/catalog.txt", "content": "one\n", "overwrite": true}),
    )?;
    assert_eq!(as_str(&committed, "status")?, "committed");
    let rejected = lease.call(
        ops::API_V1_WRITE_FILE,
        json!({"path": "occ/catalog.txt", "content": "two\n", "overwrite": false}),
    )?;
    assert_eq!(as_str(&rejected, "status")?, "rejected");
    assert_eq!(conflict_reason(&rejected), "create_only_existing");
    let missing_edit = lease.call(
        ops::API_V1_EDIT_FILE,
        json!({
            "path": "occ/missing.txt",
            "edits": [{"old_text": "x", "new_text": "y", "replace_all": false}]
        }),
    )?;
    assert_eq!(conflict_reason(&missing_edit), "aborted_version");
    Ok(())
}
