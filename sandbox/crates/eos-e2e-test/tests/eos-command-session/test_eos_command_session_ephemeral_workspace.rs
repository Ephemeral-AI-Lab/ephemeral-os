//! Background-PTY command-session semantics (spec point 5).
//!
//! The session exit condition is process-GROUP based: a foreground command that
//! backgrounds a same-pgid child stays RUNNING until ALL members exit (a fresh-ns
//! exec is NEWUSER|NEWNS only, so the runner scope-waits on the whole group). A
//! `write_stdin` terminate (or cancel) kills the entire group, and no descendant
//! is left behind. All children use BOUNDED sleeps + unique markers so any
//! early-return leak self-heals and never collides with another test.

use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use eos_e2e_test::{unique_suffix, NodeLease};
use eos_protocol::ops;
use serde_json::json;

use crate::support::{array, as_i64, as_str, live_pool_or_skip, stdout, wait_for_session_count};

#[test]
fn exec_simple() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({"cmd": "true", "yield_time_ms": 1000, "timeout_seconds": 5}),
    )?;
    assert_eq!(as_str(&exec, "status")?, "ok");
    assert_eq!(as_i64(&exec, "exit_code")?, 0);
    assert_eq!(stdout(&exec), "");
    Ok(())
}

#[test]
fn lingering_child_keeps_session_running() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // The foreground finishes (prints "done") but backgrounds a same-pgid child:
    // the session must stay running and uncollectable while the child lives.
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "sh -c 'echo up; sleep 30 & echo done'",
            "yield_time_ms": 1000,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(
        as_str(&exec, "status")?,
        "running",
        "a lingering background child must keep the session running: {exec}"
    );
    assert!(
        stdout(&exec).contains("done"),
        "the foreground must have completed before yield: {exec}"
    );
    let id = as_str(&exec, "command_session_id")?.to_owned();

    let count = lease.call_ok(ops::API_V1_COMMAND_SESSION_COUNT, json!({}))?;
    assert_eq!(as_i64(&count, "count")?, 1, "session must be live: {count}");

    let collected = lease.call_ok(
        ops::API_V1_COMMAND_COLLECT_COMPLETED,
        json!({"command_session_ids": [id.clone()]}),
    )?;
    assert!(
        array(&collected, "completions")?.is_empty(),
        "session must not be finalized while the child lives: {collected}"
    );

    cancel(&lease, &id)?;
    Ok(())
}

#[test]
fn session_completes_only_after_all_subprocesses_exit() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "sh -c 'sleep 3 & echo started'",
            "yield_time_ms": 800,
            "timeout_seconds": 60,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&exec, "status")?, "running", "{exec}");
    let id = as_str(&exec, "command_session_id")?.to_owned();

    // The completion must NOT arrive before the 3s child exits; it does once the
    // whole process group is gone (exit condition = all subprocesses complete).
    let deadline = Instant::now() + Duration::from_secs(8);
    let completion = loop {
        let collected = lease.call_ok(
            ops::API_V1_COMMAND_COLLECT_COMPLETED,
            json!({"command_session_ids": [id.clone()]}),
        )?;
        if let Some(completion) = array(&collected, "completions")?.first() {
            break completion.clone();
        }
        if Instant::now() >= deadline {
            cancel(&lease, &id)?;
            bail!("session never completed after the background child exited");
        }
        std::thread::sleep(Duration::from_millis(100));
    };
    assert_eq!(completion["command_session_id"], json!(id));
    assert_eq!(
        completion
            .get("result")
            .and_then(|result| result.get("status"))
            .and_then(serde_json::Value::as_str),
        Some("ok"),
        "completion must report ok once all subprocesses exited: {completion}"
    );
    wait_for_session_count(&lease, 0)?;
    Ok(())
}

#[test]
fn write_stdin_terminate_kills_whole_session() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // A stdin reader PLUS a same-pgid background sleeper: a terminate must kill the
    // entire group, not just the foreground reader.
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "sh -c 'sleep 60 & python3 -u -c \"import sys; print(\\\"ready\\\", flush=True); sys.stdin.readline(); import time; time.sleep(60)\"'",
            "yield_time_ms": 1500,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&exec, "status")?, "running", "{exec}");
    let id = as_str(&exec, "command_session_id")?.to_owned();

    // Terminate kills the whole session, so its hardened outcome is
    // success:false; use `call` to read the terminal envelope, not `call_ok`.
    let terminated = lease.call(
        ops::API_V1_WRITE_STDIN,
        json!({
            "command_session_id": id,
            "chars": "",
            "terminate": true,
            "yield_time_ms": 2000,
            "max_output_tokens": 1000
        }),
    )?;
    assert!(
        matches!(as_str(&terminated, "status")?, "cancelled" | "ok" | "error"),
        "terminate must drive the session to a terminal status: {terminated}"
    );
    wait_for_session_count(&lease, 0)?;
    Ok(())
}

#[test]
fn cancel_reaps_lingering_descendant() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let marker = format!("eos_e2e_orphan_{}", unique_suffix().replace('-', "_"));
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!("bash -lc 'bash -c \"exec -a {marker} sleep 60\" & echo descendant-ready; wait'"),
            "yield_time_ms": 500,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&exec, "status")?, "running", "{exec}");
    let id = as_str(&exec, "command_session_id")?.to_owned();
    assert!(
        marker_count(&lease, &marker)? > 0,
        "the descendant must be alive before cancel"
    );

    cancel(&lease, &id)?;
    // The group-targeted terminate must reap the same-pgid descendant: no orphan.
    wait_for_marker_count(&lease, &marker, 0)?;
    Ok(())
}

fn cancel(lease: &NodeLease<'_>, id: &str) -> Result<()> {
    lease.call(
        ops::API_V1_COMMAND_CANCEL,
        json!({"command_session_id": id, "max_output_tokens": 1000}),
    )?;
    Ok(())
}

/// Count container processes whose argv contains `marker`, scanning `/proc` from
/// the host PID namespace (where a reparented orphan would still be visible).
/// Runs `python3` directly (no shell wrapper) and excludes its own pid, so the
/// scanner — which itself carries `marker` in argv — never self-counts.
fn marker_count(lease: &NodeLease<'_>, marker: &str) -> Result<i64> {
    let script = format!(
        r#"import os, pathlib
marker = {marker:?}
count = 0
for proc in pathlib.Path("/proc").iterdir():
    if not proc.name.isdigit() or int(proc.name) == os.getpid():
        continue
    try:
        cmdline = proc.joinpath("cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
    except OSError:
        continue
    if marker in cmdline:
        count += 1
print(count)
"#
    );
    let output = lease.container().exec(&["python3", "-c", &script])?;
    output
        .trim()
        .parse::<i64>()
        .with_context(|| format!("parse marker count from {output:?}"))
}

fn wait_for_marker_count(lease: &NodeLease<'_>, marker: &str, expected: i64) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let count = marker_count(lease, marker)?;
        if count == expected {
            return Ok(());
        }
        if Instant::now() >= deadline {
            bail!("marker {marker} count did not reach {expected}; last {count}");
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}
