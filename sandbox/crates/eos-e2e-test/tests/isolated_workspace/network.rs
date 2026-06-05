//! Isolated-workspace network isolation (spec point 3).
//!
//! An isolated session runs in its OWN network namespace (veth + net fd), while
//! ephemeral execs share the container netns. So an ephemeral server and an
//! isolated server can bind the SAME port with no conflict, whereas two ephemeral
//! servers on the same port collide (EADDRINUSE).
//!
//! Robustness: each test picks a UNIQUE port (cross-run collisions impossible),
//! servers are bounded by `timeout` (a leak self-heals fast), assertions use
//! `ensure!` so the cleanup that cancels servers / exits the isolated session
//! runs even on failure, and the isolated session for caller B is always exited.

use anyhow::{ensure, Context, Result};
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{as_str, live_pool_or_skip, reset_isolated_workspaces, stdout};

/// A high, per-run-unique port so a server that outlives one test (e.g. its
/// `timeout` window) never collides with the same fixed port in the next run.
fn unique_port() -> u16 {
    let hash = eos_e2e_test::unique_suffix()
        .bytes()
        .fold(0_u32, |acc, byte| acc.wrapping_mul(31).wrapping_add(u32::from(byte)));
    40_000 + u16::try_from(hash % 8_000).unwrap_or(0)
}

fn start_server(lease: &eos_e2e_test::NodeLease<'_>, caller_id: Option<&str>, port: u16) -> Result<Value> {
    let cmd = format!(
        "mkdir -p /eos/scratch/e2e && timeout 20 python3 -m http.server {port} >/eos/scratch/e2e/srv-{port}.log 2>&1"
    );
    let mut args = json!({
        "cmd": cmd,
        "yield_time_ms": 400,
        "timeout_seconds": 60,
        "max_output_tokens": 500
    });
    if let Some(caller) = caller_id {
        args["caller_id"] = json!(caller);
    }
    lease.call_ok(ops::API_V1_EXEC_COMMAND, args)
}

fn cancel(lease: &eos_e2e_test::NodeLease<'_>, caller_id: Option<&str>, id: &str) {
    let mut args = json!({"command_session_id": id, "max_output_tokens": 200});
    if let Some(caller) = caller_id {
        args["caller_id"] = json!(caller);
    }
    let _ = lease.call(ops::API_V1_COMMAND_CANCEL, args);
}

#[test]
fn cross_mode_same_port_no_conflict() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_workspaces(&lease);
    let port = unique_port();
    let caller_b = format!("iws-net-b-{}", eos_e2e_test::unique_suffix());

    // Caller A: an ephemeral server holding the port in the container netns.
    let server_a = start_server(&lease, None, port)?;
    let id_a = as_str(&server_a, "command_session_id").ok().map(ToOwned::to_owned);

    let body = (|| -> Result<()> {
        ensure!(
            as_str(&server_a, "status")? == "running",
            "ephemeral server must start: {server_a}"
        );
        // Caller B enters isolated mode (its own netns), then binds the SAME port.
        lease.call_ok(
            ops::API_ISOLATED_WORKSPACE_ENTER,
            json!({"caller_id": caller_b}),
        )?;
        let bind_b = lease.call_ok(
            ops::API_V1_EXEC_COMMAND,
            json!({
                "caller_id": caller_b,
                "cmd": format!("python3 -c \"import socket,time;s=socket.socket();s.bind(('0.0.0.0',{port}));print('BOUND',flush=True);time.sleep(20)\""),
                "yield_time_ms": 600,
                "timeout_seconds": 60,
                "max_output_tokens": 500
            }),
        )?;
        ensure!(
            as_str(&bind_b, "status")? == "running",
            "isolated server must stay running: {bind_b}"
        );
        ensure!(
            stdout(&bind_b).contains("BOUND"),
            "isolated server must bind the same port in its own netns: {bind_b}"
        );
        if let Some(id_b) = bind_b.get("command_session_id").and_then(Value::as_str) {
            cancel(&lease, Some(&caller_b), id_b);
        }
        Ok(())
    })();

    if let Some(id_a) = id_a.as_deref() {
        cancel(&lease, None, id_a);
    }
    let _ = lease.call(
        ops::API_ISOLATED_WORKSPACE_EXIT,
        json!({"caller_id": caller_b, "grace_s": 0.1}),
    );
    body
}

#[test]
fn same_mode_same_port_conflicts() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let port = unique_port();

    // Two ephemeral execs share the container netns: the second bind collides.
    let server = start_server(&lease, None, port)?;
    let id = as_str(&server, "command_session_id").ok().map(ToOwned::to_owned);

    let body = (|| -> Result<()> {
        ensure!(
            as_str(&server, "status")? == "running",
            "first ephemeral server must start: {server}"
        );
        let bind = lease.call_ok(
            ops::API_V1_EXEC_COMMAND,
            json!({
                "cmd": format!("python3 -c \"import socket;socket.socket().bind(('0.0.0.0',{port}))\" && echo BOUND || echo EADDRINUSE"),
                "yield_time_ms": 2000,
                "timeout_seconds": 30,
                "max_output_tokens": 500
            }),
        )?;
        ensure!(
            stdout(&bind).contains("EADDRINUSE"),
            "a second ephemeral bind on the same port must fail (shared netns): {bind}"
        );
        ensure!(
            !stdout(&bind).contains("BOUND"),
            "the second ephemeral bind must not succeed: {bind}"
        );
        Ok(())
    })();

    if let Some(id) = id.as_deref() {
        cancel(&lease, None, id);
    }
    body
}

#[test]
fn isolated_exit_reports_dedicated_netns() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_workspaces(&lease);
    lease.call_ok(ops::API_ISOLATED_WORKSPACE_ENTER, json!({}))?;
    let exit = lease.call_ok(ops::API_ISOLATED_WORKSPACE_EXIT, json!({}))?;
    let inspection = exit.get("inspection").context("exit inspection")?;
    // Four namespace fds (user, mnt, pid, net) — the net fd is the netns proof.
    assert_eq!(
        inspection.get("ns_fd_count").and_then(Value::as_i64),
        Some(4),
        "isolated session must hold its own net namespace fd: {exit}"
    );
    let veth_host = inspection
        .get("veth_host_name")
        .and_then(Value::as_str)
        .context("veth_host_name")?;
    let veth_ns = inspection
        .get("veth_ns_name")
        .and_then(Value::as_str)
        .context("veth_ns_name")?;
    assert!(
        veth_host.starts_with("eos-iws-") && veth_host.ends_with('h'),
        "host veth name should follow the eos-iws-*h convention: {veth_host}"
    );
    assert!(
        veth_ns.starts_with("eos-iws-") && veth_ns.ends_with('n'),
        "ns veth name should follow the eos-iws-*n convention: {veth_ns}"
    );
    Ok(())
}
