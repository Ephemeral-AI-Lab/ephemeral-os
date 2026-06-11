//! OCC gating routes: `.git` is blocked, gitignored paths bypass the merge.
//!
//! The OCC service is the gate in front of the workspace LayerStack. It routes
//! every change into one of three lanes — Drop (`.git/*`, never published),
//! Direct (gitignored, no base-hash gate), or Gated (normal CAS merge). These
//! tests assert the two non-default lanes purely from the wire:
//!   - `.git/*` writes return a success/committed envelope with EMPTY
//!     `changed_paths`, and the file reads back `exists=false` (Route::Drop).
//!   - a gitignored write reports `timings.occ.commit.direct_path_count == 1`
//!     (Route::Direct), with a non-ignored control reporting `gated_path_count`.

use std::sync::{Arc, Barrier};
use std::thread;

use anyhow::Result;
use eos_e2e_test::{next_invocation_id, unique_suffix};
use eos_operation::core::ops;
use serde_json::{json, Value};

use crate::support::{array, as_bool, as_str, live_pool_or_skip};

/// Read a nested `timings.<key>` number from a response (no support helper
/// exists for nested timings; a misspelled key silently returns `None`).
fn timing_f64(value: &Value, key: &str) -> Option<f64> {
    value
        .get("timings")
        .and_then(|timings| timings.get(key))
        .and_then(Value::as_f64)
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
    let write = lease.call_ok(
        ops::SANDBOX_FILE_WRITE,
        json!({"path": path, "content": "blocked\n", "overwrite": true}),
    )?;
    // .git/* routes Route::Drop -> OccStatus::Dropped, which is_success()==true,
    // so the wire envelope is committed/success with NO published path.
    assert_eq!(as_str(&write, "status")?, "committed", "{write}");
    assert!(as_bool(&write, "success")?, "{write}");
    assert!(
        array(&write, "changed_paths")?.is_empty(),
        "a .git write must publish nothing: {write}"
    );

    let read = lease.call_ok(ops::SANDBOX_FILE_READ, json!({"path": path}))?;
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
        ops::SANDBOX_FILE_WRITE,
        json!({"path": "ignore-probe/.gitignore", "content": "*.txt\n", "overwrite": true}),
    )?;

    // An ignored path (*.txt) routes Route::Direct: no base-hash gate.
    let ignored = lease.call_ok(
        ops::SANDBOX_FILE_WRITE,
        json!({"path": "ignore-probe/secret.txt", "content": "ignored\n", "overwrite": true}),
    )?;
    assert!(as_bool(&ignored, "success")?, "{ignored}");
    assert_eq!(
        timing_f64(&ignored, "occ.commit.direct_path_count"),
        Some(1.0),
        "gitignored write must route Direct: {ignored}"
    );
    assert_eq!(
        timing_f64(&ignored, "occ.commit.gated_path_count"),
        Some(0.0),
        "gitignored write must not be gated: {ignored}"
    );

    // Control: a non-ignored sibling (.log not matched by *.txt) stays Gated.
    let tracked = lease.call_ok(
        ops::SANDBOX_FILE_WRITE,
        json!({"path": "ignore-probe/tracked.log", "content": "tracked\n", "overwrite": true}),
    )?;
    assert_eq!(
        timing_f64(&tracked, "occ.commit.gated_path_count"),
        Some(1.0),
        "non-ignored write must route Gated: {tracked}"
    );
    assert_eq!(
        timing_f64(&tracked, "occ.commit.direct_path_count"),
        Some(0.0),
        "non-ignored write must not be direct: {tracked}"
    );
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
        ops::SANDBOX_FILE_WRITE,
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
                    ops::SANDBOX_FILE_WRITE,
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
        assert!(
            as_bool(&response, "success")?,
            "ignored same-path writer should commit directly: {response}"
        );
        assert_eq!(
            timing_f64(&response, "occ.commit.direct_path_count"),
            Some(1.0),
            "ignored same-path writer must route Direct: {response}"
        );
        assert_eq!(
            timing_f64(&response, "occ.commit.gated_path_count"),
            Some(0.0),
            "ignored same-path writer must bypass Gated OCC: {response}"
        );
    }

    let read = lease.call_ok(ops::SANDBOX_FILE_READ, json!({"path": path}))?;
    let final_content = as_str(&read, "content")?;
    assert!(
        payloads.iter().any(|payload| payload == final_content),
        "final ignored-path content must be one whole writer payload: {read}"
    );
    Ok(())
}
