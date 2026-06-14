//! Isolated-workspace lifecycle tests.
//!
//! Exercises the real SetNs / ns-holder / veth / cgroup machinery via
//! `enter` → (isolated write/read) → `status` → `exit`, asserting on the op
//! responses: the manifest pin on enter/status, isolated `mutation_source`,
//! discard-on-exit (the write is never OCC-published), and the exit `inspection`
//! teardown facts.

use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use e2e_test::client::TraceWireContext;
use protocol::catalog;
use serde_json::{json, Value};
use trace::TraceRecord;

use crate::support::{
    as_bool, as_i64, as_str, envelope_result, has_trace_event, live_pool_or_skip,
    reset_isolated_workspaces, trace_record, wait_for_command_count,
    wait_for_command_stdout_contains,
};

#[test]
fn enter_status_exit_pin_and_teardown() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;

    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let handle_id = as_str(&enter, "workspace_handle_id")?.to_owned();
    let pinned_version = enter
        .get("manifest_version")
        .and_then(Value::as_i64)
        .context("enter manifest_version")?;
    let pinned_hash = as_str(&enter, "manifest_root_hash")?.to_owned();
    assert!(
        !handle_id.is_empty(),
        "enter must return a handle id: {enter}"
    );
    assert_eq!(
        pinned_hash.len(),
        64,
        "manifest_root_hash must be sha256 hex: {enter}"
    );

    // status reports the same pin while open.
    let status = lease.call_ok(catalog::SANDBOX_ISOLATION_STATUS, json!({}))?;
    assert!(
        as_bool(&status, "open")?,
        "status must report open: {status}"
    );
    assert_eq!(
        status.get("manifest_version").and_then(Value::as_i64),
        Some(pinned_version),
        "status pin must match enter: {status}"
    );

    // exit tears down and reports inspection facts.
    let exit = lease.call_ok(catalog::SANDBOX_ISOLATION_EXIT, json!({}))?;
    let inspection = exit.get("inspection").context("exit inspection")?;
    assert_eq!(
        inspection
            .get("handle_registered_after")
            .and_then(Value::as_bool),
        Some(false),
        "handle must be unregistered after exit: {exit}"
    );
    // lease_released is Option<bool>: when present it must be true.
    if let Some(released) = inspection.get("lease_released").and_then(Value::as_bool) {
        assert!(released, "isolated lease must be released on exit: {exit}");
    }
    // cgroup_exists_after is Option<bool>: when present it must be false.
    if let Some(cgroup) = inspection
        .get("cgroup_exists_after")
        .and_then(Value::as_bool)
    {
        assert!(!cgroup, "cgroup must be removed on exit: {exit}");
    }
    assert!(
        inspection
            .get("holder_kill_error")
            .map(Value::is_null)
            .unwrap_or(true),
        "ns-holder must be reaped without error: {exit}"
    );

    // status after exit reports closed.
    let closed = lease.call_ok(catalog::SANDBOX_ISOLATION_STATUS, json!({}))?;
    assert!(
        !as_bool(&closed, "open")?,
        "status must report closed: {closed}"
    );
    Ok(())
}

#[test]
fn enter_rejects_active_command_and_repeated_enter_reports_already_open() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let exec = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": "bash -lc 'printf ACTIVE; sleep 30'",
            "yield_time_ms": 500,
            "timeout_seconds": 60,}),
    )?;
    assert_eq!(as_str(&exec, "status")?, "running", "{exec}");
    let command_id = as_str(&exec, "command_id")?.to_owned();
    // `printf ACTIVE` may not reach the transcript within the 500ms yield under
    // emulation; poll until it does (still proves the command is actively live).
    wait_for_command_stdout_contains(&lease, &command_id, "ACTIVE")?;

    let body = (|| -> Result<()> {
        let rejected = lease.call(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
        assert_eq!(
            as_str(&rejected, "status")?,
            "rejected",
            "enter must reject instead of silently cleaning up an active command: {rejected}",
        );
        let rejected_error = envelope_fault(&rejected)?;
        assert_eq!(
            as_str(rejected_error, "kind")?,
            "active_background_work",
            "active command rejection should use a stable error kind: {rejected}"
        );
        assert_eq!(
            fault_detail_fields(rejected_error)?
                .get("active_commands")
                .and_then(Value::as_i64),
            Some(1),
            "rejection should report active command count: {rejected}"
        );

        lease.call(
            catalog::SANDBOX_COMMAND_CANCEL,
            json!({"command_id": command_id}),
        )?;
        wait_for_command_count(&lease, 0)?;

        let enter = lease.call(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
        assert_eq!(as_str(&enter, "status")?, "ok", "{enter}");
        let enter = envelope_result(&enter)?;
        assert!(!as_str(enter, "workspace_handle_id")?.is_empty(), "{enter}");
        let repeated = lease.call(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
        assert_eq!(
            as_str(&repeated, "status")?,
            "rejected",
            "repeated enter must reject while the handle is open: {repeated}"
        );
        let repeated_error = envelope_fault(&repeated)?;
        assert_eq!(
            as_str(repeated_error, "kind")?,
            "already_open",
            "repeated enter should report already_open: {repeated}"
        );
        lease.call_ok(catalog::SANDBOX_ISOLATION_EXIT, json!({}))?;
        Ok(())
    })();

    if body.is_err() {
        let _ = lease.call(
            catalog::SANDBOX_COMMAND_CANCEL,
            json!({"command_id": command_id}),
        );
        let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.0}));
        let _ = wait_for_command_count(&lease, 0);
    }
    body
}

#[test]
fn isolated_write_is_private_and_discarded_on_exit() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let caller_id = format!("iws-discard-{}", e2e_test::unique_suffix());
    let path = format!("private/{}.txt", e2e_test::unique_suffix());

    let enter = lease.call_ok(
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({"caller_id": caller_id}),
    )?;
    as_str(&enter, "workspace_handle_id")?;
    let write = lease.call_ok(
        catalog::SANDBOX_FILE_WRITE,
        json!({"caller_id": caller_id, "path": path, "content": "isolated private\n", "overwrite": true}),
    )?;
    assert_eq!(as_str(&write, "workspace")?, "isolated", "{write}");
    let exit = lease.call_ok(
        catalog::SANDBOX_ISOLATION_EXIT,
        json!({"caller_id": caller_id}),
    )?;
    assert!(
        as_i64(&exit, "evicted_upperdir_bytes")? > 0,
        "exit should report discarded private bytes: {exit}"
    );
    assert_eq!(
        exit["inspection"]["lease_released"],
        json!(true),
        "exit releases the snapshot lease: {exit}"
    );
    Ok(())
}

#[test]
fn live_trace_isolated_enter_exec_status_exit_records_one_chain() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_workspaces(&lease);

    let suffix = e2e_test::unique_suffix();
    let trace_id = format!("phase04-isolated-chain-{suffix}");
    let caller_id = format!("isolated-chain-{suffix}");

    let enter = lease.call_traced(
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({"caller_id": caller_id}),
        &trace_context(&trace_id, "enter"),
    )?;
    let body = (|| -> Result<()> {
        let enter_result = envelope_result(&enter)?;
        let handle_id = as_str(enter_result, "workspace_handle_id")?.to_owned();
        let enter_record = trace_record(&enter)?;

        let exec = lease.call_traced(
            catalog::SANDBOX_COMMAND_EXEC,
            json!({
                "caller_id": caller_id,
                "cmd": "bash -lc 'printf isolated-trace-chain'",
                "yield_time_ms": 15_000,
                "timeout_seconds": 30,
            }),
            &trace_context(&trace_id, "exec"),
        )?;
        let mut records = vec![enter_record, trace_record(&exec)?];
        let exec = finalize_traced_command(&lease, &trace_id, exec, &mut records)?;
        assert_eq!(as_str(&exec, "status")?, "ok", "{exec}");

        let status = lease.call_traced(
            catalog::SANDBOX_ISOLATION_STATUS,
            json!({"caller_id": caller_id}),
            &trace_context(&trace_id, "status"),
        )?;
        let status_record = trace_record(&status)?;
        let status_result = envelope_result(&status)?;
        assert!(as_bool(status_result, "open")?, "{status}");

        let heartbeat = lease.call_traced(
            catalog::SANDBOX_CALL_HEARTBEAT,
            json!({"invocation_ids": ["phase04-isolated-chain-not-inflight"]}),
            &trace_context(&trace_id, "heartbeat"),
        )?;
        let heartbeat_record = trace_record(&heartbeat)?;
        let heartbeat_result = envelope_result(&heartbeat)?;
        assert_eq!(as_i64(heartbeat_result, "touched")?, 0, "{heartbeat}");

        let exit = lease.call_traced(
            catalog::SANDBOX_ISOLATION_EXIT,
            json!({"caller_id": caller_id, "grace_s": 0.0}),
            &trace_context(&trace_id, "exit"),
        )?;
        let exit_record = trace_record(&exit)?;
        records.extend([
            status_record.clone(),
            heartbeat_record.clone(),
            exit_record.clone(),
        ]);

        assert_trace_chain(&records, &trace_id);
        assert!(
            has_trace_event(
                &records[0],
                "isolated_workspace",
                "enter_started",
                |details| {
                    details.get("caller_id").and_then(Value::as_str) == Some(caller_id.as_str())
                }
            ) && has_trace_event(
                &records[0],
                "isolated_workspace",
                "holder_started",
                |details| {
                    details.get("workspace_handle_id").and_then(Value::as_str)
                        == Some(handle_id.as_str())
                }
            ) && has_trace_event(
                &records[0],
                "isolated_workspace",
                "network_configured",
                |details| {
                    details.get("workspace_handle_id").and_then(Value::as_str)
                        == Some(handle_id.as_str())
                        && details
                            .get("dns_fallback_applied")
                            .and_then(Value::as_bool)
                            .is_some()
                }
            ),
            "enter trace must include isolated lifecycle facts: {:?}",
            records[0].events
        );
        assert!(
            records.iter().any(|record| {
                has_trace_event(record, "command", "prepared", |_| true)
                    && has_trace_event(record, "command", "spawned", |_| true)
            }),
            "exec trace must include command preparation/spawn facts"
        );
        assert!(
            has_trace_event(
                &status_record,
                "isolated_workspace",
                "status_read",
                |details| {
                    details.get("open").and_then(Value::as_bool) == Some(true)
                        && details.get("workspace_handle_id").and_then(Value::as_str)
                            == Some(handle_id.as_str())
                }
            ),
            "status trace must include open handle facts: {:?}",
            status_record.events
        );
        assert!(
            has_trace_event(
                &heartbeat_record,
                "daemon.dispatch",
                "op_resolved",
                |details| {
                    details.get("op").and_then(Value::as_str)
                        == Some(catalog::SANDBOX_CALL_HEARTBEAT)
                }
            ),
            "heartbeat-adjacent trace must include dispatch facts: {:?}",
            heartbeat_record.events
        );
        assert!(
            has_trace_event(
                &exit_record,
                "isolated_workspace",
                "exit_started",
                |details| {
                    details.get("caller_id").and_then(Value::as_str) == Some(caller_id.as_str())
                }
            ) && has_trace_event(
                &exit_record,
                "isolated_workspace",
                "teardown_phase_finished",
                |details| { details.get("phase").and_then(Value::as_str).is_some() }
            ) && has_trace_event(&exit_record, "isolated_workspace", "exited", |details| {
                details.get("workspace_handle_id").and_then(Value::as_str)
                    == Some(handle_id.as_str())
                    && details
                        .get("lease_released")
                        .and_then(Value::as_bool)
                        .is_some()
            }),
            "exit trace must include teardown facts: {:?}",
            exit_record.events
        );
        Ok(())
    })();

    if body.is_err() {
        let _ = lease.call(
            catalog::SANDBOX_ISOLATION_EXIT,
            json!({"caller_id": caller_id, "grace_s": 0.0}),
        );
    }
    body
}

fn trace_context(trace_id: &str, step: &str) -> TraceWireContext {
    TraceWireContext {
        trace_id: trace_id.to_owned(),
        request_id: format!("{trace_id}-{step}"),
        parent_span_id: None,
        link_hints: Vec::new(),
        capture_budget_version: 1,
    }
}

fn envelope_fault(response: &Value) -> Result<&Value> {
    response
        .get("error")
        .context("envelope response should include error fault")
}

fn fault_detail_fields(fault: &Value) -> Result<&Value> {
    fault
        .get("details")
        .and_then(|details| details.get("fields"))
        .context("envelope fault should include details.fields")
}

fn finalize_traced_command(
    lease: &e2e_test::NodeLease<'_>,
    trace_id: &str,
    response: Value,
    records: &mut Vec<TraceRecord>,
) -> Result<Value> {
    if as_str(&response, "status")? != "running" {
        return Ok(envelope_result(&response)?.clone());
    }
    let command_id = as_str(envelope_result(&response)?, "command_id")?.to_owned();
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut poll_index = 0;
    loop {
        poll_index += 1;
        let progress = lease.call_traced(
            catalog::SANDBOX_COMMAND_POLL,
            json!({"command_id": command_id, "last_n_lines": 50}),
            &trace_context(trace_id, &format!("poll-{poll_index}")),
        )?;
        records.push(trace_record(&progress)?);
        if as_str(&progress, "status")? != "running" {
            return Ok(envelope_result(&progress)?.clone());
        }
        if Instant::now() >= deadline {
            anyhow::bail!("command {command_id} did not finish before deadline: {progress}");
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn assert_trace_chain(records: &[TraceRecord], trace_id: &str) {
    assert!(records.len() >= 5, "expected a multi-request trace chain");
    for record in records {
        assert_eq!(record.trace_id.as_str(), trace_id);
        assert!(
            record
                .request_id
                .as_ref()
                .is_some_and(|request_id| request_id.as_str().starts_with(trace_id)),
            "request_id must preserve the chain prefix: {:?}",
            record.request_id
        );
    }
}
