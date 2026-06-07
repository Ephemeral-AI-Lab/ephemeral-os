use std::time::{Duration, Instant};

use anyhow::{bail, ensure, Context, Result};
use eos_e2e_test::NodeLease;
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{
    array, as_bool, as_str, isolated_command_session_transcript_path, live_pool_or_skip,
    reset_isolated_workspaces, stdout, wait_for_active_leases, wait_for_container_path,
    wait_for_isolated_command_session_transcript_recycled, wait_for_session_count,
};

#[test]
fn iws_same_port_discard() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let server_cmd =
        "mkdir -p /eos/scratch/e2e && python3 -m http.server 39001 >/eos/scratch/e2e/eos-e2e-http.log 2>&1";
    let first_enter = lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    let first_handle_id = as_str(&first_enter, "workspace_handle_id")?.to_owned();
    let first = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": server_cmd,
            "yield_time_ms": 100,
            "timeout_seconds": 120,
            "max_output_tokens": 500
        }),
    )?;
    assert_eq!(
        as_str(&first, "status")?,
        "running",
        "isolated command should start: {first}"
    );
    let first_id = as_str(&first, "command_session_id")?.to_owned();
    lease.call(
        ops::API_V1_COMMAND_CANCEL,
        json!({"command_session_id": &first_id}),
    )?;
    wait_for_isolated_command_session_transcript_recycled(&lease, &first_handle_id, &first_id)?;
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({"grace_s": 0.1}))?;

    let second_enter = lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    let second_handle_id = as_str(&second_enter, "workspace_handle_id")?.to_owned();
    let second = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": server_cmd,
            "yield_time_ms": 100,
            "timeout_seconds": 120,
            "max_output_tokens": 500
        }),
    )?;
    assert_eq!(
        as_str(&second, "status")?,
        "running",
        "same isolated port should be reusable after exit discard: {second}"
    );
    if let Some(id) = second
        .get("command_session_id")
        .and_then(serde_json::Value::as_str)
    {
        lease.call(
            ops::API_V1_COMMAND_CANCEL,
            json!({"command_session_id": id}),
        )?;
        wait_for_isolated_command_session_transcript_recycled(&lease, &second_handle_id, id)?;
    }
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({"grace_s": 0.1}))?;
    Ok(())
}

#[test]
fn iws_prompt_stdin_poll_cancel_private_discard() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let path = format!(
        "iws-command-session/prompt-{}.txt",
        eos_e2e_test::unique_suffix().replace('-', "_")
    );
    let cmd = format!(
        "python3 -u -c 'import pathlib,sys,time; \
print(\"iws-prompt\", flush=True); \
payload=sys.stdin.readline().strip(); \
path=pathlib.Path({path:?}); \
path.parent.mkdir(parents=True, exist_ok=True); \
path.write_text(payload + \"\\n\"); \
print(\"iws-wrote:\" + payload, flush=True); \
time.sleep(60)'"
    );

    let enter = lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    let handle_id = as_str(&enter, "workspace_handle_id")?.to_owned();
    let started = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({
            "cmd": cmd,
            "yield_time_ms": 500,
            "timeout_seconds": 120,
            "max_output_tokens": 1000
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running" && stdout(&started).contains("iws-prompt"),
        "isolated prompt command should start and expose prompt: {started}"
    );
    let session_id = as_str(&started, "command_session_id")?.to_owned();
    let transcript_path = isolated_command_session_transcript_path(&handle_id, &session_id);
    wait_for_container_path(&lease, &transcript_path, true, Duration::from_secs(3))?;

    let body = (|| -> Result<()> {
        let answered = lease.call_ok(
            ops::API_V1_WRITE_STDIN,
            json!({
                "command_session_id": session_id,
                "chars": "private-payload\n",
                "yield_time_ms": 1500,
                "max_output_tokens": 1000
            }),
        )?;
        ensure!(
            !stdout(&answered).contains("iws-prompt"),
            "stdin cursor must not replay the already-consumed prompt: {answered}"
        );
        let reply = if stdout(&answered).contains("iws-wrote:private-payload") {
            answered
        } else {
            poll_stdin_cursor_until_stdout_contains(
                &lease,
                &session_id,
                "iws-wrote:private-payload",
                "iws-prompt",
                Instant::now() + Duration::from_secs(15),
            )?
        };
        ensure!(
            stdout(&reply).contains("iws-wrote:private-payload"),
            "stdin write should drive the isolated prompt command: {reply}"
        );

        let read_private = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": &path}))?;
        ensure!(
            as_str(&read_private, "workspace")? == "isolated",
            "read while isolated should route through isolated workspace: {read_private}"
        );
        ensure!(
            as_str(&read_private, "content")? == "private-payload\n",
            "isolated command-session write should be visible while open: {read_private}"
        );

        let quiet = lease.call_ok(
            ops::API_V1_WRITE_STDIN,
            json!({
                "command_session_id": session_id,
                "chars": "",
                "yield_time_ms": 250,
                "max_output_tokens": 1000
            }),
        )?;
        ensure!(
            !stdout(&quiet).contains("iws-wrote:private-payload"),
            "empty poll must not replay consumed isolated command output: {quiet}"
        );

        let not_done = lease.call_ok(
            ops::API_V1_COMMAND_COLLECT_COMPLETED,
            json!({"command_session_ids": [session_id.clone()]}),
        )?;
        ensure!(
            array(&not_done, "completions")?.is_empty(),
            "sleeping isolated command should not collect before cancellation: {not_done}"
        );

        let cancelled = lease.call(
            ops::API_V1_COMMAND_CANCEL,
            json!({"command_session_id": session_id, "max_output_tokens": 1000}),
        )?;
        ensure!(
            matches!(as_str(&cancelled, "status")?, "cancelled" | "ok" | "error"),
            "isolated command cancel should return terminal-ish status: {cancelled}"
        );
        wait_for_session_count(&lease, 0)?;
        wait_for_isolated_command_session_transcript_recycled(&lease, &handle_id, &session_id)?;
        Ok(())
    })();

    if body.is_err() {
        let _ = lease.call(
            ops::API_V1_COMMAND_CANCEL,
            json!({"command_session_id": session_id, "max_output_tokens": 1000}),
        );
    }
    let exit = lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({"grace_s": 0.1}));
    body?;
    exit?;

    let after_exit = lease.call_ok(ops::API_V1_READ_FILE, json!({"path": &path}))?;
    ensure!(
        as_str(&after_exit, "workspace")? == "ephemeral",
        "read after isolated exit should route back to ephemeral workspace: {after_exit}"
    );
    ensure!(
        !as_bool(&after_exit, "exists")?,
        "isolated command-session write should be discarded after exit: {after_exit}"
    );
    wait_for_active_leases(&lease, 0)?;
    Ok(())
}

fn poll_stdin_cursor_until_stdout_contains(
    lease: &NodeLease<'_>,
    session_id: &str,
    needle: &str,
    forbidden_replay: &str,
    deadline: Instant,
) -> Result<Value> {
    let mut last = None;
    while Instant::now() < deadline {
        let poll = lease.call_ok(
            ops::API_V1_WRITE_STDIN,
            json!({
                "command_session_id": session_id,
                "chars": "",
                "yield_time_ms": 250,
                "max_output_tokens": 1000
            }),
        )?;
        ensure!(
            !stdout(&poll).contains(forbidden_replay),
            "stdin cursor poll must not replay isolated prompt output: {poll}"
        );
        if stdout(&poll).contains(needle) {
            return Ok(poll);
        }
        last = Some(poll);
    }
    bail!("stdin cursor did not surface {needle:?} before deadline; last poll: {last:?}");
}

#[test]
fn setsid_descendant_reaped_on_isolated_exit() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_workspaces(&lease);
    let marker = format!(
        "eos_e2e_iws_escape_{}",
        eos_e2e_test::unique_suffix().replace('-', "_")
    );
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    // The same escaped-`setsid` descendant that LEAKS in ephemeral mode is reaped
    // here: isolated workspaces run commands under a cgroup, and exit's cgroup kill
    // reaps even a pgid-escaped descendant. This is the contained counterpart that
    // proves the ephemeral-vs-isolated asymmetry. (Requires cgroup delegation in
    // the live container; without it the descendant would survive and this fails,
    // which is itself the finding.)
    let body = (|| -> Result<()> {
        let completed = lease.call_ok(
            ops::API_V1_EXEC_COMMAND,
            json!({
                "cmd": format!(
                    "bash -lc 'setsid bash -c \"exec -a {marker} sleep 30\" >/dev/null 2>&1 & echo iws-escaped-ready'"
                ),
                "yield_time_ms": 1500,
                "timeout_seconds": 60,
                "max_output_tokens": 1000
            }),
        )?;
        ensure!(
            as_str(&completed, "status")? == "ok",
            "isolated escaped-child command should complete: {completed}"
        );
        ensure!(
            marker_count(&lease, &marker)? >= 1,
            "escaped descendant should be alive before isolated exit"
        );
        wait_for_session_count(&lease, 0)?;
        Ok(())
    })();

    // Always exit isolated mode so a tripped assertion cannot leak an open
    // workspace past the cap; exit's cgroup kill is also what reaps the escapee.
    let exit = lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({"grace_s": 0.1}));
    body?;
    exit?;
    // The isolated cgroup must reap the escaped descendant on exit.
    wait_for_marker_count(&lease, &marker, 0, Duration::from_secs(6))?;
    wait_for_active_leases(&lease, 0)?;
    Ok(())
}

/// Count container processes whose argv carries `marker`, scanned from the host
/// PID namespace where a reparented escapee remains visible; excludes the
/// scanner's own pid (its argv carries `marker` too).
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
        std::thread::sleep(Duration::from_millis(50));
    }
}
