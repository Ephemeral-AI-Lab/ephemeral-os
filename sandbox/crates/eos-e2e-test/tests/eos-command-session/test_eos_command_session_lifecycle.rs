use std::thread;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use eos_e2e_test::{unique_suffix, NodeLease};
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{
    array, as_i64, as_str, command_session_transcript_logs, command_session_transcript_path,
    live_pool_or_skip, stdout, wait_for_active_leases,
    wait_for_command_session_transcript_recycled, wait_for_container_path, wait_for_session_count,
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
    let cancelled = lease.call(
        ops::API_V1_COMMAND_CANCEL,
        json!({"command_session_id": id, "max_output_tokens": 1000}),
    )?;
    wait_for_command_session_transcript_recycled(lease, id)?;
    Ok(cancelled)
}

fn process_marker() -> String {
    format!(
        "eos_e2e_command_session_{}",
        unique_suffix().replace('-', "_")
    )
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
            "command_session_id": &id,
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
            "command_session_id": &id,
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
            "command_session_id": &id,
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
            json!({"command_session_ids": [&id]}),
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
        json!({"command_session_ids": [&id]}),
    )?;
    assert!(
        array(&redelivered, "completions")?.is_empty(),
        "collect_completed should remove delivered completions: {redelivered}"
    );
    wait_for_command_session_transcript_recycled(&lease, &id)?;
    Ok(())
}

#[test]
fn finite_exec_before_yield_recycles_transient_transcript_file() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let before = command_session_transcript_logs(&lease)?;
    let marker = format!(
        "finite-transcript-{}",
        eos_e2e_test::unique_suffix().replace('-', "_")
    );
    let completed = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!("printf '{marker}\\n'"),
            "yield_time_ms": 3000,
            "timeout_seconds": 30,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(
        as_str(&completed, "status")?,
        "ok",
        "finite command should complete inside the initial yield: {completed}"
    );
    assert!(
        completed.get("command_session_id").is_none(),
        "finite command should not expose a background session handle: {completed}"
    );
    assert!(
        stdout(&completed).contains(&marker),
        "finite command should return stdout in the initial response: {completed}"
    );
    wait_for_session_count(&lease, 0)?;
    wait_for_active_leases(&lease, 0)?;
    let after = command_session_transcript_logs(&lease)?;
    assert_eq!(
        after, before,
        "finite command may create an internal transcript, but it must recycle it before returning"
    );
    Ok(())
}

#[test]
fn completed_session_removes_transcript_file() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "sh -c 'echo transcript-start; sleep 1; echo transcript-end'",
            "yield_time_ms": 100,
            "timeout_seconds": 30,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&started, "status")?, "running", "{started}");
    let id = as_str(&started, "command_session_id")?.to_owned();
    let transcript_path = command_session_transcript_path(&id);
    wait_for_container_path(&lease, &transcript_path, true, Duration::from_secs(3))?;

    let completion = collect_completion(&lease, &id, Duration::from_secs(10))?;
    let result = completion.get("result").context("completion result")?;
    assert!(
        stdout(result).contains("transcript-end"),
        "completion should carry the final stdout: {completion}"
    );
    wait_for_session_count(&lease, 0)?;
    wait_for_container_path(&lease, &transcript_path, false, Duration::from_secs(3))?;
    wait_for_active_leases(&lease, 0)?;
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
    // A timed-out command is killed, so its hardened outcome is success:false;
    // use `call` to read the structured terminal envelope rather than `call_ok`.
    let exec = lease.call(
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
    cancel_session(&lease, id)?;
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

    // Terminate kills the session, so its hardened outcome is success:false;
    // use `call` to read the structured terminal envelope rather than `call_ok`.
    let terminated = lease.call(
        ops::API_V1_WRITE_STDIN,
        json!({
            "command_session_id": &id,
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
    wait_for_command_session_transcript_recycled(&lease, &id)?;
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

/// Send `signal` to every container process whose argv carries `marker`, from a
/// process fully outside the command session (a container `python3` exec). This
/// is the "killed by another process" path: termination that did NOT come from
/// the `cancel`/`write_stdin terminate` API. The scanner excludes its own pid so
/// it never signals itself (its argv carries `marker` too).
fn kill_marker(lease: &NodeLease<'_>, marker: &str, signal: i32) -> Result<()> {
    let script = format!(
        r#"import os, pathlib
marker = {marker:?}
for proc in pathlib.Path("/proc").iterdir():
    if not proc.name.isdigit() or int(proc.name) == os.getpid():
        continue
    try:
        cmdline = proc.joinpath("cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
    except OSError:
        continue
    if marker in cmdline:
        try:
            os.kill(int(proc.name), {signal})
        except OSError:
            pass
"#
    );
    lease.container().exec(&["python3", "-c", &script])?;
    Ok(())
}

/// Poll `collect_completed` until the session parks a terminal completion. A
/// fire-and-forget session (no live poller) finalizes through the reaper, so the
/// completion arrives asynchronously and must be polled for.
fn collect_completion(lease: &NodeLease<'_>, id: &str, within: Duration) -> Result<Value> {
    let deadline = Instant::now() + within;
    loop {
        let collected = lease.call_ok(
            ops::API_V1_COMMAND_COLLECT_COMPLETED,
            json!({ "command_session_ids": [id] }),
        )?;
        if let Some(completion) = array(&collected, "completions")?.first() {
            return Ok(completion.clone());
        }
        if Instant::now() >= deadline {
            bail!("session {id} never parked a completion within {within:?}");
        }
        thread::sleep(Duration::from_millis(100));
    }
}

/// A process that died by signal surfaces a signal-coded exit: `runner.rs`
/// encodes it as a negative code (`-signal`), and a wrapping shell re-encodes the
/// same death as `128 + signal`. Either form distinguishes a kill from a clean or
/// ordinary nonzero exit.
fn signal_coded_exit(exit_code: i64) -> bool {
    !(0..128).contains(&exit_code)
}

#[test]
fn external_signal_kill_is_structured() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let marker = process_marker();
    // A separate container process SIGKILLs the foreground out from under the
    // session — no cancel/terminate API call is involved. The runner must reap the
    // signal death, finalize the session, park exactly one completion, and release
    // the lease.
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!("bash -lc 'echo kill-ready; exec -a {marker} sleep 60'"),
            "yield_time_ms": 1000,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&started, "status")?, "running", "{started}");
    assert!(stdout(&started).contains("kill-ready"), "{started}");
    let id = as_str(&started, "command_session_id")?.to_owned();
    wait_for_marker_at_least(&lease, &marker, 1)?;

    kill_marker(&lease, &marker, 9)?;

    let completion = collect_completion(&lease, &id, Duration::from_secs(10))?;
    let result = completion.get("result").context("completion result")?;
    assert_ne!(
        as_str(result, "status")?,
        "ok",
        "an externally killed session must not report ok: {completion}"
    );
    assert!(
        signal_coded_exit(as_i64(result, "exit_code")?),
        "external SIGKILL should surface a signal-coded exit_code: {completion}"
    );
    wait_for_session_count(&lease, 0)?;
    wait_for_command_session_transcript_recycled(&lease, &id)?;
    wait_for_active_leases(&lease, 0)?;
    wait_for_marker_count(&lease, &marker, 0, Duration::from_secs(3))?;
    Ok(())
}

#[test]
fn self_kill_reports_signal_exit() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // The command kills its own process-group leader; termination is driven by the
    // process itself, not by the cancel API, but must still surface a signal-coded
    // terminal exit. Fast self-kill usually completes within the yield window, so
    // read the structured envelope with `call` rather than `call_ok`.
    let exec = lease.call(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "sh -c 'echo bye; kill -9 $$'",
            "yield_time_ms": 2000,
            "timeout_seconds": 30,
            "max_output_tokens": 1000
        }),
    )?;
    if as_str(&exec, "status")? == "running" {
        let id = as_str(&exec, "command_session_id")?.to_owned();
        let completion = collect_completion(&lease, &id, Duration::from_secs(10))?;
        let result = completion.get("result").context("completion result")?;
        assert_ne!(as_str(result, "status")?, "ok", "{completion}");
        assert!(
            signal_coded_exit(as_i64(result, "exit_code")?),
            "self-kill should surface a signal-coded exit_code: {completion}"
        );
        wait_for_command_session_transcript_recycled(&lease, &id)?;
    } else {
        assert_ne!(
            as_str(&exec, "status")?,
            "ok",
            "a self-killed command must not report ok: {exec}"
        );
        assert!(
            signal_coded_exit(as_i64(&exec, "exit_code")?),
            "self-kill should surface a signal-coded exit_code: {exec}"
        );
    }
    wait_for_session_count(&lease, 0)?;
    wait_for_active_leases(&lease, 0)?;
    Ok(())
}

#[test]
fn external_kill_of_foreground_keeps_group_running() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let fg = process_marker();
    let peer = format!("{}_peer", process_marker());
    // A foreground plus a same-pgid background peer. Killing ONLY the foreground by
    // external signal must NOT finalize the session: the pgid scope-wait keeps it
    // running until the surviving peer also exits. This is the intersection of
    // "killed by other process" and "remains running".
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": format!(
                "bash -lc 'bash -c \"exec -a {peer} sleep 60\" & echo group-ready; exec -a {fg} sleep 60'"
            ),
            "yield_time_ms": 1000,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&started, "status")?, "running", "{started}");
    assert!(stdout(&started).contains("group-ready"), "{started}");
    let id = as_str(&started, "command_session_id")?.to_owned();
    wait_for_marker_at_least(&lease, &fg, 1)?;
    wait_for_marker_at_least(&lease, &peer, 1)?;

    kill_marker(&lease, &fg, 9)?;
    wait_for_marker_count(&lease, &fg, 0, Duration::from_secs(3))?;

    // Peer still alive keeps the pgid non-empty, so the session stays running and
    // does not finalize.
    let count = lease.call_ok(ops::API_V1_COMMAND_SESSION_COUNT, json!({}))?;
    assert_eq!(
        as_i64(&count, "count")?,
        1,
        "a surviving same-pgid peer must keep the session running: {count}"
    );
    let still = lease.call_ok(
        ops::API_V1_COMMAND_COLLECT_COMPLETED,
        json!({ "command_session_ids": [id.clone()] }),
    )?;
    assert!(
        array(&still, "completions")?.is_empty(),
        "session must not finalize while the peer lives: {still}"
    );

    // The peer now exits too, so the scope-wait empties and the session finalizes.
    kill_marker(&lease, &peer, 9)?;
    wait_for_marker_count(&lease, &peer, 0, Duration::from_secs(3))?;
    let completion = collect_completion(&lease, &id, Duration::from_secs(10))?;
    assert_eq!(completion["command_session_id"], json!(&id), "{completion}");
    wait_for_session_count(&lease, 0)?;
    wait_for_command_session_transcript_recycled(&lease, &id)?;
    wait_for_active_leases(&lease, 0)?;
    Ok(())
}

#[test]
fn write_stdin_to_completed_session_is_structured() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // A session that finishes on its own and is left uncollected. A late
    // write_stdin against the finished id must return a structured terminal
    // envelope (not a hang or a running zombie), distinct from the not-found error
    // returned for an id that never existed.
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "sh -c 'echo quick; sleep 1'",
            "yield_time_ms": 100,
            "timeout_seconds": 30,
            "max_output_tokens": 1000
        }),
    )?;
    let id = as_str(&started, "command_session_id")?.to_owned();
    // Count returning to zero means the session left the live registry (finished);
    // its completion is parked but uncollected.
    wait_for_session_count(&lease, 0)?;
    wait_for_command_session_transcript_recycled(&lease, &id)?;

    let late = lease.call(
        ops::API_V1_WRITE_STDIN,
        json!({
            "command_session_id": &id,
            "chars": "late\n",
            "yield_time_ms": 200,
            "max_output_tokens": 200
        }),
    )?;
    assert!(
        matches!(as_str(&late, "status")?, "ok" | "error" | "cancelled"),
        "write_stdin to a finished session must return a structured terminal status: {late}"
    );

    // Drain the parked completion so a recycled container starts clean.
    lease.call_ok(
        ops::API_V1_COMMAND_COLLECT_COMPLETED,
        json!({ "command_session_ids": [&id] }),
    )?;
    wait_for_active_leases(&lease, 0)?;
    Ok(())
}

#[test]
fn sigint_char_interrupts_foreground() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    // A Ctrl-C (`\x03`) char through write_stdin (NOT terminate) drives the
    // interrupt path: the session detects the char, sends SIGINT to the process
    // group, and reaches a non-ok terminal result distinct from terminate's
    // SIGTERM/SIGKILL path. Depending on shell timing, the runner can observe the
    // signal as exit 130 or as the shell's nonzero interrupted-command status.
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": "bash -lc 'echo sigint-ready; exec sleep 60'",
            "yield_time_ms": 1000,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    assert_eq!(as_str(&started, "status")?, "running", "{started}");
    assert!(stdout(&started).contains("sigint-ready"), "{started}");
    let id = as_str(&started, "command_session_id")?.to_owned();

    let interrupted = lease.call(
        ops::API_V1_WRITE_STDIN,
        json!({
            "command_session_id": &id,
            "chars": "\u{3}",
            "yield_time_ms": 2000,
            "max_output_tokens": 1000
        }),
    )?;
    let result = if as_str(&interrupted, "status")? == "running" {
        let completion = collect_completion(&lease, &id, Duration::from_secs(10))?;
        completion
            .get("result")
            .context("completion result")?
            .clone()
    } else {
        interrupted
    };
    assert_ne!(
        as_str(&result, "status")?,
        "ok",
        "a Ctrl-C interrupt must not finalize as ok: {result}"
    );
    let exit_code = as_i64(&result, "exit_code")?;
    assert!(
        exit_code == 1 || exit_code == 130 || signal_coded_exit(exit_code),
        "Ctrl-C should finalize with a nonzero interrupt-shaped exit code: {result}"
    );
    wait_for_session_count(&lease, 0)?;
    wait_for_command_session_transcript_recycled(&lease, &id)?;
    wait_for_active_leases(&lease, 0)?;
    Ok(())
}
