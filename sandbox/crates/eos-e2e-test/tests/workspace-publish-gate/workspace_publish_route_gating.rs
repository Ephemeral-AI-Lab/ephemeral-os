//! OCC gating routes: `.git` is blocked, gitignored paths bypass the merge.
//!
//! The OCC service is the gate in front of the workspace LayerStack. It routes
//! every change into one of three lanes — Drop (`.git/*`, never published),
//! Direct (gitignored, no base-hash gate), or Gated (normal CAS merge). These
//! tests assert the two non-default lanes through result payloads plus trace
//! events:
//!   - `.git/*` writes return a success/committed response with EMPTY
//!     `changed_paths`, and the file reads back `exists=false` (Route::Drop).
//!   - a gitignored write records an OCC trace event with
//!     `direct_path_count == 1` (Route::Direct), with a non-ignored control
//!     recording `gated_path_count`.

use std::sync::{Arc, Barrier};
use std::thread;

use anyhow::{bail, Result};
use eos_e2e_test::{next_invocation_id, unique_suffix};
use eos_operation::core::catalog;
use serde_json::{json, Value};

use crate::support::{
    array, as_bool, as_str, envelope_result, has_trace_event, live_pool_or_skip, trace_record,
};

fn assert_occ_commit_counts(
    response: &Value,
    direct_path_count: f64,
    gated_path_count: f64,
    dropped_file_count: f64,
) -> Result<()> {
    let record = trace_record(response)?;
    if has_trace_event(&record, "occ", "commit_finished", |details| {
        number_field(details, "direct_path_count") == Some(direct_path_count)
            && number_field(details, "gated_path_count") == Some(gated_path_count)
            && number_field(details, "dropped_file_count") == Some(dropped_file_count)
    }) {
        return Ok(());
    }
    bail!(
        "OCC commit trace did not report direct={direct_path_count}, gated={gated_path_count}, dropped={dropped_file_count}: {:?}",
        record.events
    )
}

fn number_field(value: &Value, key: &str) -> Option<f64> {
    value.get(key).and_then(Value::as_f64)
}

#[test]
fn git_writes_are_dropped_and_unreadable() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // A novel .git path that no `git init` would create, so a later exists=false
    // read positively proves the Drop (rather than a coincidentally-absent file).
    let path = ".git/eos-probe.txt";
    let write_wire = lease.call(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": path, "content": "blocked\n", "overwrite": true}),
    )?;
    let write = envelope_result(&write_wire)?;
    // .git/* routes Route::Drop -> OccStatus::Dropped, so the result payload is
    // committed/success with NO published path.
    assert_eq!(as_str(&write, "status")?, "committed", "{write}");
    assert!(as_bool(&write, "success")?, "{write}");
    assert!(
        array(&write, "changed_paths")?.is_empty(),
        "a .git write must publish nothing: {write}"
    );
    assert_occ_commit_counts(&write_wire, 0.0, 0.0, 1.0)?;

    let read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": path}))?;
    assert!(
        !as_bool(&read, "exists")?,
        "a dropped .git write must not be readable: {read}"
    );
    Ok(())
}

#[test]
fn gitignored_writes_bypass_the_occ_gate() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // A per-DIRECTORY .gitignore keeps the routing effect scoped to ignore-probe/
    // so the warm pooled node is not globally polluted. The .gitignore file is
    // itself a normal (gated) write.
    lease.call_ok(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "ignore-probe/.gitignore", "content": "*.txt\n", "overwrite": true}),
    )?;

    // An ignored path (*.txt) routes Route::Direct: no base-hash gate.
    let ignored_wire = lease.call(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "ignore-probe/secret.txt", "content": "ignored\n", "overwrite": true}),
    )?;
    let ignored = envelope_result(&ignored_wire)?;
    assert!(as_bool(&ignored, "success")?, "{ignored}");
    assert_occ_commit_counts(&ignored_wire, 1.0, 0.0, 0.0)?;

    // Control: a non-ignored sibling (.log not matched by *.txt) stays Gated.
    let tracked_wire = lease.call(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": "ignore-probe/tracked.log", "content": "tracked\n", "overwrite": true}),
    )?;
    let tracked = envelope_result(&tracked_wire)?;
    assert!(as_bool(&tracked, "success")?, "{tracked}");
    assert_occ_commit_counts(&tracked_wire, 0.0, 1.0, 0.0)?;
    Ok(())
}

#[test]
fn concurrent_gitignored_same_path_direct_writes() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let dir = format!("ignore-race-{}", unique_suffix());
    let path = format!("{dir}/same.txt");
    lease.call_ok(
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": format!("{dir}/.gitignore"), "content": "*.txt\n", "overwrite": true}),
    )?;

    let payloads: Vec<String> = (0..6)
        .map(|index| {
            let marker = format!("writer-{index}:");
            format!("{marker}\n{}\n", marker.repeat(128))
        })
        .collect();
    let barrier = Arc::new(Barrier::new(payloads.len()));
    let handles: Vec<_> = payloads
        .iter()
        .cloned()
        .map(|content| {
            let client = lease.client().clone();
            let root = lease.root().to_owned();
            let caller_id = lease.caller_id().to_owned();
            let path = path.clone();
            let barrier = Arc::clone(&barrier);
            thread::spawn(move || {
                barrier.wait();
                client.request(
                    catalog::SANDBOX_FILE_WRITE,
                    &next_invocation_id(),
                    &json!({
                        "layer_stack_root": root,
                        "caller_id": caller_id,
                        "path": path,
                        "content": content,
                        "overwrite": true
                    }),
                )
            })
        })
        .collect();

    for handle in handles {
        let response = handle.join().expect("writer thread panicked")?;
        let result = envelope_result(&response)?;
        assert!(
            as_bool(result, "success")?,
            "ignored same-path writer should commit directly: {result}"
        );
        assert_occ_commit_counts(&response, 1.0, 0.0, 0.0)?;
    }

    let read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": path}))?;
    let final_content = as_str(&read, "content")?;
    assert!(
        payloads.iter().any(|payload| payload == final_content),
        "final ignored-path content must be one whole writer payload: {read}"
    );
    Ok(())
}
