use std::thread;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use eos_e2e_test::{unique_suffix, NodeLease};
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{
    array, as_i64, as_str, live_pool_or_skip, stdout, wait_for_active_leases,
    wait_for_session_count,
};

fn start_sleeping_session(lease: &eos_e2e_test::NodeLease<'_>, marker: &str) -> Result<String> {
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!("sh -c 'echo {marker}; sleep 60'"),
            "yield_time_ms": 500,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&started, "status")?, "running");
    assert!(
        stdout(&started).contains(marker),
        "session should print marker before returning: {started}"
    );
    Ok(as_str(&started, "command_session_id")?.to_owned())
}

fn cancel_session(lease: &eos_e2e_test::NodeLease<'_>, id: &str) -> Result<Value> {
    lease.call_ok(
        ops::API_V1_COMMAND_CANCEL,
        json!({"command_session_id": id, "max_output_tokens": 1000}),
    )
}

fn process_marker() -> String {
    format!("eos_e2e_core_{}", unique_suffix().replace('-', "_"))
}

#[test]
fn exec_returns_session_id() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let id = start_sleeping_session(&lease, "session-started")?;
    assert!(!id.is_empty());
    cancel_session(&lease, &id)?;
    Ok(())
}

#[test]
fn write_stdin_echo() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "python3 -u -c 'import sys,time; print(\"ready\", flush=True); line=sys.stdin.readline().strip(); print(\"got:\" + line, flush=True); time.sleep(60)'",
            "yield_time_ms": 500,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    let id = as_str(&started, "command_session_id")?.to_owned();
    let stdin = lease.call_ok(
        ops::API_V1_WRITE_STDIN,
        json!({
            "command_session_id": id,
            "chars": "payload\n",
            "yield_time_ms": 2000,
            "max_output_tokens": 1000
        }),
    )?;
    assert!(
        stdout(&stdin).contains("got:payload"),
        "stdin write should return command output: {stdin}"
    );
    cancel_session(&lease, &id)?;
    Ok(())
}

#[test]
fn command_session_output_cursor_no_replay() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "python3 -u -c 'import sys,time; print(\"cursor-first\", flush=True); line=sys.stdin.readline().strip(); print(\"cursor-second:\" + line, flush=True); time.sleep(60)'",
            "yield_time_ms": 500,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&started, "status")?, "running");
    assert!(
        stdout(&started).contains("cursor-first"),
        "initial poll should return first output: {started}"
    );
    let id = as_str(&started, "command_session_id")?.to_owned();

    let second = lease.call_ok(
        ops::API_V1_WRITE_STDIN,
        json!({
            "command_session_id": id,
            "chars": "payload\n",
            "yield_time_ms": 1500,
            "max_output_tokens": 1000
        }),
    )?;
    assert!(
        stdout(&second).contains("cursor-second:payload"),
        "stdin poll should return newly produced output: {second}"
    );
    assert!(
        !stdout(&second).contains("cursor-first"),
        "stdin poll must not replay already consumed output: {second}"
    );

    let quiet = lease.call_ok(
        ops::API_V1_WRITE_STDIN,
        json!({
            "command_session_id": id,
            "chars": "",
            "yield_time_ms": 300,
            "max_output_tokens": 1000
        }),
    )?;
    assert!(
        !stdout(&quiet).contains("cursor-first")
            && !stdout(&quiet).contains("cursor-second:payload"),
        "empty follow-up poll must not replay consumed output: {quiet}"
    );
    cancel_session(&lease, &id)?;
    Ok(())
}

#[test]
fn collect_completed_drains() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "sh -c 'echo queued; sleep 1; echo done'",
            "yield_time_ms": 100,
            "timeout_seconds": 10,
            "max_output_tokens": 1000
        }),
    )?;
    let id = as_str(&started, "command_session_id")?.to_owned();
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let collected = lease.call_ok(
            ops::API_V1_COMMAND_COLLECT_COMPLETED,
            json!({"command_session_ids": [id]}),
        )?;
        let completions = array(&collected, "completions")?;
        if let Some(completion) = completions.first() {
            assert_eq!(completion["command_session_id"], id);
            assert!(
                stdout(completion.get("result").context("completion result")?).contains("done"),
                "completion should carry final stdout: {completion}"
            );
            break;
        }
        if Instant::now() >= deadline {
            bail!("session completion was not parked before deadline");
        }
        thread::sleep(Duration::from_millis(100));
    }
    let redelivered = lease.call_ok(
        ops::API_V1_COMMAND_COLLECT_COMPLETED,
        json!({"command_session_ids": [id]}),
    )?;
    assert!(
        array(&redelivered, "completions")?.is_empty(),
        "collect_completed should remove delivered completions: {redelivered}"
    );
    Ok(())
}

#[test]
fn cancel_unblocks() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let id = start_sleeping_session(&lease, "cancel-ready")?;
    let cancel = cancel_session(&lease, &id)?;
    assert!(
        matches!(as_str(&cancel, "status")?, "cancelled" | "error" | "ok"),
        "cancel should return a terminal-ish status: {cancel}"
    );
    Ok(())
}

#[test]
fn session_count_accuracy() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let first = start_sleeping_session(&lease, "count-one")?;
    let second = start_sleeping_session(&lease, "count-two")?;
    let count = lease.call_ok(ops::API_V1_COMMAND_SESSION_COUNT, json!({}))?;
    assert_eq!(
        as_i64(&count, "count")?,
        2,
        "two live sessions expected: {count}"
    );
    cancel_session(&lease, &first)?;
    cancel_session(&lease, &second)?;
    let count = lease.call_ok(ops::API_V1_COMMAND_SESSION_COUNT, json!({}))?;
    assert_eq!(
        as_i64(&count, "count")?,
        0,
        "cancel should remove sessions: {count}"
    );
    Ok(())
}

#[test]
fn exec_timeout() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({"cmd": "sleep 2", "yield_time_ms": 2500, "timeout_seconds": 1}),
    )?;
    assert!(
        matches!(as_str(&exec, "status")?, "timeout" | "error" | "cancelled"),
        "timeout path should return a non-ok status: {exec}"
    );
    Ok(())
}

#[test]
fn output_token_cap() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let exec = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "python3 - <<'PY'\nimport sys, time\nsys.stdout.write('x' * 20000)\nsys.stdout.flush()\ntime.sleep(60)\nPY",
            "yield_time_ms": 500,
            "timeout_seconds": 120,
            "max_output_tokens": 20
        }),
    )?;
    assert_eq!(as_str(&exec, "status")?, "running");
    assert!(
        stdout(&exec).len() < 20_000,
        "max_output_tokens should cap returned stdout: {} bytes",
        stdout(&exec).len()
    );
    let id = as_str(&exec, "command_session_id")?;
    lease.call_ok(
        ops::API_V1_COMMAND_CANCEL,
        json!({"command_session_id": id}),
    )?;
    Ok(())
}

#[test]
fn cancel_by_invocation_id_reports_already_done_for_idle_id() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let cancel = lease.call_ok(
        ops::API_V1_CANCEL,
        json!({"invocation_id": "eos-e2e-not-running"}),
    )?;
    assert_eq!(cancel["already_done"], Value::Bool(true));
    assert_eq!(cancel["cancelled"], Value::Bool(false));
    Ok(())
}

#[test]
fn write_stdin_terminate_reaps_marker_process() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let marker = process_marker();
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!(
                "bash -lc 'bash -c \"exec -a {marker} sleep 60\" & python3 -u -c \"import sys,time; print(\\\"terminate-ready\\\", flush=True); sys.stdin.readline(); time.sleep(60)\"'"
            ),
            "yield_time_ms": 1500,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&started, "status")?, "running", "{started}");
    assert!(
        stdout(&started).contains("terminate-ready"),
        "stdin reader should be ready before terminate: {started}"
    );
    let id = as_str(&started, "command_session_id")?.to_owned();
    wait_for_marker_at_least(&lease, &marker, 1)?;

    let terminated = lease.call_ok(
        ops::API_V1_WRITE_STDIN,
        json!({
            "command_session_id": id,
            "chars": "",
            "terminate": true,
            "yield_time_ms": 3000,
            "max_output_tokens": 1000
        }),
    )?;
    assert!(
        matches!(as_str(&terminated, "status")?, "cancelled" | "ok" | "error"),
        "terminate should return a terminal status: {terminated}"
    );
    wait_for_session_count(&lease, 0)?;
    wait_for_active_leases(&lease, 0)?;
    wait_for_marker_count(&lease, &marker, 0, Duration::from_secs(3))?;
    Ok(())
}

#[test]
fn nohup_child_keeps_session_running() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let marker = process_marker();
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!(
                "bash -lc 'nohup bash -c \"exec -a {marker} sleep 60\" >/dev/null 2>&1 & echo nohup-ready'"
            ),
            "yield_time_ms": 1000,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(
        as_str(&started, "status")?,
        "running",
        "plain nohup stays in the runner process group and keeps the session live: {started}"
    );
    let id = as_str(&started, "command_session_id")?.to_owned();
    wait_for_session_stdout(&lease, &id, &started, "nohup-ready")?;
    wait_for_marker_at_least(&lease, &marker, 1)?;

    let collected = lease.call_ok(
        ops::API_V1_COMMAND_COLLECT_COMPLETED,
        json!({"command_session_ids": [id.clone()]}),
    )?;
    assert!(
        array(&collected, "completions")?.is_empty(),
        "nohup child in the same process group must not finalize early: {collected}"
    );

    cancel_session(&lease, &id)?;
    wait_for_marker_count(&lease, &marker, 0, Duration::from_secs(3))?;
    Ok(())
}

#[test]
fn setsid_nohup_contract() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let marker = process_marker();
    let completed = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!(
                "bash -lc 'setsid nohup bash -c \"exec -a {marker} sleep 4\" >/dev/null 2>&1 & echo setsid-ready'"
            ),
            "yield_time_ms": 2000,
            "timeout_seconds": 20,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(
        as_str(&completed, "status")?,
        "ok",
        "setsid nohup escapes the runner process group, so the protocol command completes: {completed}"
    );
    assert!(
        completed.get("command_session_id").is_none(),
        "completed setsid command must not leave a command session handle: {completed}"
    );
    assert!(
        stdout(&completed).contains("setsid-ready"),
        "foreground shell should report the detached launch: {completed}"
    );
    wait_for_session_count(&lease, 0)?;
    wait_for_marker_at_least(&lease, &marker, 1)?;
    wait_for_marker_count(&lease, &marker, 0, Duration::from_secs(6))?;
    Ok(())
}

fn wait_for_session_stdout(
    lease: &NodeLease<'_>,
    session_id: &str,
    initial: &Value,
    marker: &str,
) -> Result<()> {
    if stdout(initial).contains(marker) {
        return Ok(());
    }

    let deadline = Instant::now() + Duration::from_secs(5);
    let mut last = initial.clone();
    loop {
        if Instant::now() >= deadline {
            bail!("session output never contained {marker}: {last}");
        }
        let poll = lease.call_ok(
            ops::API_V1_WRITE_STDIN,
            json!({
                "command_session_id": session_id,
                "chars": "",
                "yield_time_ms": 250,
                "max_output_tokens": 1000
            }),
        )?;
        if stdout(&poll).contains(marker) {
            return Ok(());
        }
        last = poll;
        thread::sleep(Duration::from_millis(50));
    }
}

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

fn wait_for_marker_at_least(lease: &NodeLease<'_>, marker: &str, minimum: i64) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let count = marker_count(lease, marker)?;
        if count >= minimum {
            return Ok(());
        }
        if Instant::now() >= deadline {
            bail!("marker {marker} count did not reach {minimum}; last {count}");
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn wait_for_marker_count(
    lease: &NodeLease<'_>,
    marker: &str,
    expected: i64,
    timeout: Duration,
) -> Result<()> {
    let deadline = Instant::now() + timeout;
    loop {
        let count = marker_count(lease, marker)?;
        if count == expected {
            return Ok(());
        }
        if Instant::now() >= deadline {
            bail!("marker {marker} count did not reach {expected}; last {count}");
        }
        thread::sleep(Duration::from_millis(50));
    }
}
