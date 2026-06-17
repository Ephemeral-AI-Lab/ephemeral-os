use anyhow::{ensure, Context, Result};
use protocol::catalog;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::support::{
    as_bool, as_i64, as_str, envelope_error_kind, has_trace_event, live_pool_or_skip,
    reset_isolated_networks, trace_record, wait_for_active_leases, wait_for_command_count,
    wait_for_command_stdout_contains,
};

#[test]
fn compact_remount_open_isolated_network_reclaims_old_lower_chain() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let path = format!("compact-remount/{}.txt", e2e_test::unique_suffix());

    for index in 0..10 {
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": path,
                "content": format!("public-{index}\n"),
                "overwrite": true,
            }),
        )?;
    }
    let before_enter = lease.call_ok(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    ensure!(
        as_i64(&before_enter, "manifest_depth")? >= 10,
        "test needs a retained public lower chain before enter: {before_enter}"
    );

    lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let body = (|| -> Result<()> {
        let private_path = format!("compact-remount/private-{}.txt", e2e_test::unique_suffix());
        let private_write = lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": private_path,
                "content": "private-upper\n",
                "overwrite": true,
            }),
        )?;
        ensure!(
            !as_bool(&private_write, "published")?,
            "private upperdir write should not publish before remount: {private_write}"
        );

        let remount = lease.call_ok(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({
                "probe_path": &path,
                "probe_content": "public-9\n",
            }),
        )?;
        ensure!(
            as_i64(&remount, "compacted_snapshot_layers")? >= 10,
            "remount should compact the multi-layer leased snapshot: {remount}"
        );
        ensure!(
            as_i64(&remount, "remounted_layer_count")? == 1,
            "session should remount onto one compact lowerdir: {remount}"
        );
        ensure!(
            remount
                .get("lease_retargeted")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            "lease refcounts should be retargeted to the compact checkpoint: {remount}"
        );
        ensure!(
            remount
                .get("remount_staged_switch")
                .and_then(Value::as_bool)
                == Some(true)
                && remount
                    .get("remount_staging_verified")
                    .and_then(Value::as_bool)
                    == Some(true)
                && remount
                    .get("remount_rollback_unmounted")
                    .and_then(Value::as_bool)
                == Some(true),
            "remount should verify staging, switch, and old-mount cleanup before lease retarget: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            remount
                .get("squash_manifest_version")
                .is_some_and(|value| !value.is_null()),
            "active public head should squash after lease retarget: {remount}"
        );
        ensure!(
            as_i64(&remount, "after_layer_dirs")? <= 3,
            "old lower chain should be reclaimed to a constant number of dirs: {remount}"
        );
        ensure!(
            as_i64(&remount, "after_manifest_depth")? <= 1,
            "active manifest should be compact after remount: {remount}"
        );
        ensure!(
            as_i64(&remount, "active_leases_after")? == 1,
            "open session lease should remain active after remount: {remount}"
        );

        let public_read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": path}))?;
        ensure!(
            as_str(&public_read, "content")? == "public-9\n",
            "remounted session should retain public snapshot content: {public_read}"
        );
        let private_read =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": private_path}))?;
        ensure!(
            as_str(&private_read, "content")? == "private-upper\n",
            "remount should preserve the existing private upperdir: {private_read}"
        );

        let metrics = lease.call_ok(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
        ensure!(
            as_i64(&metrics, "layer_dirs")? <= 3,
            "metrics should also show bounded layer dirs after remount: {metrics}"
        );
        ensure!(
            as_i64(&metrics, "active_leases")? == 1,
            "session should still hold exactly one lease: {metrics}"
        );
        Ok(())
    })();
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_blocks_while_isolated_command_is_running() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);

    for index in 0..6 {
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": "compact-remount/active-command.txt",
                "content": format!("public-{index}\n"),
                "overwrite": true,
            }),
        )?;
    }
    lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": "bash -lc 'printf REMOUNT_ACTIVE; sleep 30'",
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live isolated command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();
    wait_for_command_stdout_contains(&lease, &command_id, "REMOUNT_ACTIVE")?;

    let body = (|| -> Result<()> {
        let blocked = lease.call(catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT, json!({}))?;
        ensure!(
            envelope_error_kind(&blocked)? == "lease_remount_blocked",
            "live remount must reject while the command session is active: {blocked}"
        );
        let fields = blocked
            .get("error")
            .and_then(|error| error.get("details"))
            .and_then(|details| details.get("fields"))
            .context("blocked response should include detail fields")?;
        assert_blocked_remount_reports_pressure_only(fields)?;
        ensure!(
            as_str(fields, "reason")? == "cwd_pinned_workspace",
            "blocked response must expose concrete inspected reason: {blocked}"
        );
        ensure!(
            as_i64(fields, "active_commands")? == 1,
            "blocked response must report active command count: {blocked}"
        );
        ensure!(
            as_i64(fields, "process_count")? >= 1,
            "blocked response must report inspected process count: {blocked}"
        );
        ensure!(
            as_i64(fields, "quiesced_process_count")? >= 1,
            "blocked response must report quiesced process count: {blocked}"
        );
        ensure!(
            as_i64(fields, "pinned_cwd_count")? >= 1,
            "blocked response must report cwd pinning: {blocked}"
        );
        ensure!(
            fields
                .get("process_group_ids")
                .and_then(Value::as_array)
                .is_some_and(|ids| !ids.is_empty()),
            "blocked response must report process group ids: {blocked}"
        );
        ensure!(
            fields.get("inspected").and_then(Value::as_bool) == Some(true),
            "blocked response must report successful inspection: {blocked}"
        );
        ensure!(
            fields.get("quiesce_attempted").and_then(Value::as_bool) == Some(true),
            "blocked response must report quiesce attempt: {blocked}"
        );
        ensure!(
            fields.get("resumed").and_then(Value::as_bool) == Some(true),
            "blocked response must resume the process group before returning: {blocked}"
        );
        ensure!(
            as_i64(fields, "lease_layer_count")? >= 1,
            "blocked response must report protected lease layer count: {blocked}"
        );
        let record = trace_record(&blocked)?;
        assert_blocked_trace_reports_pressure_only(&record)?;
        ensure!(
            has_trace_event(&record, "layer_stack", "lease_remount_planned", |details| {
                details["remount_state"] == "remount_pending"
                    && details["lease_layer_count"].as_i64().unwrap_or_default() >= 1
                    && details["active_depth_before"].as_i64().unwrap_or_default() >= 1
            }),
            "blocked remount response must trace lease_remount_planned: {record:?}"
        );
        ensure!(
            has_trace_event(&record, "layer_stack", "lease_remount_blocked", |details| {
                details["remount_state_at_start"] == "remount_pending"
                    && details["remount_state_after"] == "active"
                    && details["reason"] == "cwd_pinned_workspace"
                    && details["active_commands"] == 1
                    && details["pinned_cwd_count"].as_i64().unwrap_or_default() >= 1
                    && details["quiesced_process_count"]
                        .as_i64()
                        .unwrap_or_default()
                        >= 1
                    && details["inspected"] == true
                    && details["resumed"] == true
                    && details["active_leases_after"] == 1
            }),
            "blocked remount response must trace lease_remount_blocked: {record:?}"
        );

        let progress = lease.call_ok(
            catalog::SANDBOX_COMMAND_POLL,
            json!({"command_id": command_id, "last_n_lines": 20}),
        )?;
        ensure!(
            as_str(&progress, "status")? == "running",
            "blocked remount must leave the command running: {progress}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_blocks_when_remountable_command_holds_workspace_fd() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let public_path = format!("compact-remount/fd-{}.txt", e2e_test::unique_suffix());

    for index in 0..6 {
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": public_path,
                "content": format!("public-fd-{index}\n"),
                "overwrite": true,
            }),
        )?;
    }
    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command = format!(
        "bash -lc 'exec 3< {workspace_root}/{public_path}; printf REMOUNT_FD_READY; sleep 30'"
    );
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable isolated command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();
    wait_for_command_stdout_contains(&lease, &command_id, "REMOUNT_FD_READY")?;

    let body = (|| -> Result<()> {
        let blocked = lease.call(catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT, json!({}))?;
        ensure!(
            envelope_error_kind(&blocked)? == "lease_remount_blocked",
            "live remount must reject while a workspace fd is pinned: {blocked}"
        );
        let fields = blocked
            .get("error")
            .and_then(|error| error.get("details"))
            .and_then(|details| details.get("fields"))
            .context("blocked response should include detail fields")?;
        assert_blocked_remount_reports_pressure_only(fields)?;
        ensure!(
            as_str(fields, "reason")? == "fd_pinned_workspace",
            "blocked response must expose fd pinning: {blocked}"
        );
        ensure!(
            as_i64(fields, "active_commands")? == 1 && as_i64(fields, "remountable_commands")? == 1,
            "blocked response must report the opted-in command: {blocked}"
        );
        ensure!(
            as_i64(fields, "pinned_fd_count")? >= 1,
            "blocked response must report pinned workspace fds: {blocked}"
        );
        ensure!(
            as_i64(fields, "pinned_cwd_count")? == 0,
            "fd-pinned command should keep cwd outside workspace: {blocked}"
        );
        ensure!(
            fields.get("resumed").and_then(Value::as_bool) == Some(true),
            "blocked response must resume the process group before returning: {blocked}"
        );
        let record = trace_record(&blocked)?;
        assert_blocked_trace_reports_pressure_only(&record)?;
        ensure!(
            has_trace_event(&record, "layer_stack", "lease_remount_blocked", |details| {
                details["reason"] == "fd_pinned_workspace"
                    && details["remount_state_at_start"] == "remount_pending"
                    && details["remount_state_after"] == "active"
                    && details["remountable_commands"] == 1
                    && details["pinned_fd_count"].as_i64().unwrap_or_default() >= 1
                    && details["pinned_cwd_count"].as_i64().unwrap_or_default() == 0
                    && details["resumed"] == true
                    && details["active_leases_after"] == 1
            }),
            "blocked fd remount response must trace lease_remount_blocked: {record:?}"
        );

        let progress = lease.call_ok(
            catalog::SANDBOX_COMMAND_POLL,
            json!({"command_id": command_id, "last_n_lines": 20}),
        )?;
        ensure!(
            as_str(&progress, "status")? == "running",
            "blocked fd remount must leave the command running: {progress}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_blocks_when_remountable_command_maps_workspace_file() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let public_path = format!("compact-remount/map-{}.txt", e2e_test::unique_suffix());

    for index in 0..6 {
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": public_path,
                "content": format!("public-map-{index}\n"),
                "overwrite": true,
            }),
        )?;
    }
    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command = format!(
        "python3 - <<'PY'\nimport ctypes, os, time\npath = '{workspace_root}/{public_path}'\nsize = os.path.getsize(path)\nfd = os.open(path, os.O_RDONLY)\nlibc = ctypes.CDLL(None, use_errno=True)\nlibc.mmap.restype = ctypes.c_void_p\nlibc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]\naddr = libc.mmap(None, size, 1, 2, fd, 0)\nif addr == ctypes.c_void_p(-1).value:\n    err = ctypes.get_errno()\n    os.close(fd)\n    raise OSError(err, 'mmap failed')\nos.close(fd)\nctypes.string_at(addr, 1)\nprint('REMOUNT_MAP_READY', flush=True)\ntime.sleep(30)\nPY"
    );
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable isolated command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();
    wait_for_command_stdout_contains(&lease, &command_id, "REMOUNT_MAP_READY")?;

    let body = (|| -> Result<()> {
        let blocked = lease.call(catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT, json!({}))?;
        ensure!(
            envelope_error_kind(&blocked)? == "lease_remount_blocked",
            "live remount must reject while a workspace mapping is pinned: {blocked}"
        );
        let fields = blocked
            .get("error")
            .and_then(|error| error.get("details"))
            .and_then(|details| details.get("fields"))
            .context("blocked response should include detail fields")?;
        assert_blocked_remount_reports_pressure_only(fields)?;
        ensure!(
            as_str(fields, "reason")? == "mapped_file_pinned_workspace",
            "blocked response must expose mapped-file pinning: {blocked}"
        );
        ensure!(
            as_i64(fields, "active_commands")? == 1 && as_i64(fields, "remountable_commands")? == 1,
            "blocked response must report the opted-in command: {blocked}"
        );
        ensure!(
            as_i64(fields, "pinned_mapped_file_count")? >= 1,
            "blocked response must report pinned mapped workspace files: {blocked}"
        );
        ensure!(
            as_i64(fields, "pinned_fd_count")? == 0,
            "mapped-file command should close workspace fds after mapping: {blocked}"
        );
        ensure!(
            as_i64(fields, "pinned_cwd_count")? == 0,
            "mapped-file command should keep cwd outside workspace: {blocked}"
        );
        ensure!(
            fields.get("resumed").and_then(Value::as_bool) == Some(true),
            "blocked response must resume the process group before returning: {blocked}"
        );
        let record = trace_record(&blocked)?;
        assert_blocked_trace_reports_pressure_only(&record)?;
        ensure!(
            has_trace_event(&record, "layer_stack", "lease_remount_blocked", |details| {
                details["reason"] == "mapped_file_pinned_workspace"
                    && details["remountable_commands"] == 1
                    && details["pinned_mapped_file_count"]
                        .as_i64()
                        .unwrap_or_default()
                        >= 1
                    && details["pinned_cwd_count"].as_i64().unwrap_or_default() == 0
                    && details["resumed"] == true
                    && details["active_leases_after"] == 1
            }),
            "blocked mapped-file remount response must trace lease_remount_blocked: {record:?}"
        );

        let progress = lease.call_ok(
            catalog::SANDBOX_COMMAND_POLL,
            json!({"command_id": command_id, "last_n_lines": 20}),
        )?;
        ensure!(
            as_str(&progress, "status")? == "running",
            "blocked mapped-file remount must leave the command running: {progress}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_blocks_mixed_safe_and_fd_pinned_remountable_commands() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_path = format!("compact-remount/mixed-public-{suffix}.txt");

    for index in 0..8 {
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": public_path,
                "content": format!("mixed-public-{index}\n{}", "M".repeat(64 * 1024)),
                "overwrite": true,
            }),
        )?;
    }
    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let safe_command = format!(
        "bash -lc 'set -euo pipefail; printf MIXED_SAFE_READY; read -r _; test -f \"{workspace_root}/{public_path}\"; printf MIXED_SAFE_AFTER; sleep 30'"
    );
    let pinned_command = format!(
        "bash -lc 'set -euo pipefail; exec 3< \"{workspace_root}/{public_path}\"; printf MIXED_PIN_READY; read -r _; head -c 12 <&3 >/tmp/mixed-pin-read; printf MIXED_PIN_AFTER; sleep 30'"
    );

    let started_safe = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": safe_command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    let started_pinned = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": pinned_command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    ensure!(
        as_str(&started_safe, "status")? == "running"
            && as_str(&started_pinned, "status")? == "running",
        "test requires both remountable commands to be running: {started_safe} {started_pinned}"
    );
    let safe_command_id = as_str(&started_safe, "command_id")?.to_owned();
    let pinned_command_id = as_str(&started_pinned, "command_id")?.to_owned();
    wait_for_command_stdout_contains(&lease, &safe_command_id, "MIXED_SAFE_READY")?;
    wait_for_command_stdout_contains(&lease, &pinned_command_id, "MIXED_PIN_READY")?;

    let body = (|| -> Result<()> {
        let blocked = lease.call(catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT, json!({}))?;
        ensure!(
            envelope_error_kind(&blocked)? == "lease_remount_blocked",
            "mixed command remount must reject while any command pins the old mount: {blocked}"
        );
        let fields = blocked
            .get("error")
            .and_then(|error| error.get("details"))
            .and_then(|details| details.get("fields"))
            .context("blocked response should include detail fields")?;
        assert_blocked_remount_reports_pressure_only(fields)?;
        ensure!(
            as_str(fields, "reason")? == "fd_pinned_workspace",
            "mixed block should surface the unsafe pinned fd reason: {blocked}"
        );
        ensure!(
            as_i64(fields, "active_commands")? == 2 && as_i64(fields, "remountable_commands")? == 2,
            "blocked response must report both opted-in commands: {blocked}"
        );
        ensure!(
            as_i64(fields, "process_count")? >= 2
                && as_i64(fields, "quiesced_process_count")? == as_i64(fields, "process_count")?,
            "blocked mixed remount must quiesce all command processes: {blocked}"
        );
        ensure!(
            as_i64(fields, "pinned_fd_count")? >= 1 && as_i64(fields, "pinned_cwd_count")? == 0,
            "only the fd-pinned command should pin the workspace: {blocked}"
        );
        ensure!(
            fields.get("resumed").and_then(Value::as_bool) == Some(true),
            "blocked mixed remount must resume all command groups: {blocked}"
        );
        ensure!(
            as_i64(fields, "after_layer_dirs")? == as_i64(fields, "before_layer_dirs")?,
            "mixed blocked remount must not partially retarget or reclaim the leased chain: {blocked}"
        );
        let record = trace_record(&blocked)?;
        assert_blocked_trace_reports_pressure_only(&record)?;
        ensure!(
            has_trace_event(&record, "layer_stack", "lease_remount_blocked", |details| {
                details["reason"] == "fd_pinned_workspace"
                    && details["active_commands"] == 2
                    && details["remountable_commands"] == 2
                    && details["pinned_fd_count"].as_i64().unwrap_or_default() >= 1
                    && details["resumed"] == true
                    && details["active_leases_after"] == 1
            }),
            "mixed block response must trace lease_remount_blocked: {record:?}"
        );

        lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": safe_command_id, "chars": "go\n", "yield_time_ms": 1000}),
        )?;
        lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": pinned_command_id, "chars": "go\n", "yield_time_ms": 1000}),
        )?;
        wait_for_command_stdout_contains(&lease, &safe_command_id, "MIXED_SAFE_AFTER")?;
        wait_for_command_stdout_contains(&lease, &pinned_command_id, "MIXED_PIN_AFTER")?;
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": safe_command_id}),
    );
    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": pinned_command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_blocks_when_process_membership_changes_during_inspection() -> Result<()> {
    compact_remount_blocks_for_forced_reason("process_membership_changed")
}

#[test]
fn compact_remount_blocks_when_mountinfo_verification_mismatches() -> Result<()> {
    compact_remount_blocks_for_forced_reason("mountinfo_mismatch")
}

fn compact_remount_blocks_for_forced_reason(reason: &'static str) -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let public_path = format!("compact-remount/forced-{}.txt", e2e_test::unique_suffix());

    for index in 0..6 {
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": public_path,
                "content": format!("public-forced-{index}\n"),
                "overwrite": true,
            }),
        )?;
    }
    lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": "bash -lc 'printf REMOUNT_FORCE_READY; sleep 30'",
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable isolated command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();
    wait_for_command_stdout_contains(&lease, &command_id, "REMOUNT_FORCE_READY")?;

    let body = (|| -> Result<()> {
        let blocked = lease.call(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({"test_force_block_reason": reason}),
        )?;
        ensure!(
            envelope_error_kind(&blocked)? == "lease_remount_blocked",
            "forced unsafe live remount must reject: {blocked}"
        );
        let fields = blocked
            .get("error")
            .and_then(|error| error.get("details"))
            .and_then(|details| details.get("fields"))
            .context("blocked response should include detail fields")?;
        assert_blocked_remount_reports_pressure_only(fields)?;
        ensure!(
            as_str(fields, "reason")? == reason,
            "blocked response must expose forced reason {reason}: {blocked}"
        );
        ensure!(
            as_i64(fields, "active_commands")? == 1 && as_i64(fields, "remountable_commands")? == 1,
            "blocked response must report the opted-in command: {blocked}"
        );
        ensure!(
            fields.get("quiesce_attempted").and_then(Value::as_bool) == Some(true),
            "forced block must still run quiesce inspection: {blocked}"
        );
        ensure!(
            fields.get("resumed").and_then(Value::as_bool) == Some(true),
            "forced block must resume the process group before returning: {blocked}"
        );
        ensure!(
            as_i64(fields, "after_layer_dirs")? == as_i64(fields, "before_layer_dirs")?,
            "forced block must not reclaim the protected lease chain: {blocked}"
        );
        if reason == "process_membership_changed" {
            ensure!(
                fields.get("inspected").and_then(Value::as_bool) == Some(false),
                "membership-change block should report inspection as unsafe: {blocked}"
            );
        } else {
            ensure!(
                fields.get("inspected").and_then(Value::as_bool) == Some(true)
                    && as_i64(fields, "mountinfo_checked_count")? >= 1,
                "mountinfo mismatch block should report checked mountinfo: {blocked}"
            );
        }
        let record = trace_record(&blocked)?;
        assert_blocked_trace_reports_pressure_only(&record)?;
        ensure!(
            has_trace_event(&record, "layer_stack", "lease_remount_blocked", |details| {
                details["reason"] == reason
                    && details["remountable_commands"] == 1
                    && details["quiesce_attempted"] == true
                    && details["resumed"] == true
                    && details["active_leases_after"] == 1
            }),
            "forced block response must trace lease_remount_blocked: {record:?}"
        );

        let progress = lease.call_ok(
            catalog::SANDBOX_COMMAND_POLL,
            json!({"command_id": command_id, "last_n_lines": 20}),
        )?;
        ensure!(
            as_str(&progress, "status")? == "running",
            "forced blocked remount must leave the command running: {progress}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_live_remounts_explicitly_remountable_command() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let public_path = format!("compact-remount/live-{}.txt", e2e_test::unique_suffix());
    let private_path = format!(
        "compact-remount/live-private-{}.txt",
        e2e_test::unique_suffix()
    );

    for index in 0..6 {
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": public_path,
                "content": format!("public-live-{index}\n"),
                "overwrite": true,
            }),
        )?;
    }
    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command = format!(
        "bash -lc 'printf LIVE_REMOUNT_READY; read -r _; cat {workspace_root}/{public_path}; printf LIVE_REMOUNT_AFTER; printf live-private > {workspace_root}/{private_path}; sleep 30'"
    );
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable isolated command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();
    wait_for_command_stdout_contains(&lease, &command_id, "LIVE_REMOUNT_READY")?;

    let body = (|| -> Result<()> {
        let remount = lease.call_ok(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({
                "probe_path": &public_path,
                "probe_content": "public-live-5\n",
            }),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true),
            "remount should use the live command path: {remount}"
        );
        ensure!(
            remount.get("mount_verified").and_then(Value::as_bool) == Some(true),
            "remount should verify the switched mount before retarget: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            remount
                .get("remount_staged_switch")
                .and_then(Value::as_bool)
                == Some(true)
                && remount
                    .get("remount_staging_verified")
                    .and_then(Value::as_bool)
                    == Some(true)
                && remount
                    .get("remount_rollback_unmounted")
                    .and_then(Value::as_bool)
                    == Some(true),
            "live remount should use the staged switch and unmount the old rollback mount: {remount}"
        );
        ensure!(
            remount.get("remount_probe_read_ok").and_then(Value::as_bool) == Some(true)
                && remount
                    .get("remount_probe_content_matched")
                    .and_then(Value::as_bool)
                    == Some(true),
            "remount should prove the expected public file is visible through the switched mount: {remount}"
        );
        ensure!(
            remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "live remount should retarget the lease after mount verification: {remount}"
        );
        ensure!(
            remount.get("process_resumed").and_then(Value::as_bool) == Some(true),
            "live remount should resume the quiesced command before returning: {remount}"
        );
        ensure!(
            as_i64(&remount, "remountable_commands")? == 1,
            "remount should report the opted-in command: {remount}"
        );
        ensure!(
            as_i64(&remount, "process_count")? >= 1,
            "remount should inspect the command process group: {remount}"
        );
        ensure!(
            as_i64(&remount, "quiesced_process_count")? >= 1,
            "remount should quiesce the command process group: {remount}"
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "remountable command should not be pinned to the old mount: {remount}"
        );
        ensure!(
            as_i64(&remount, "after_layer_dirs")? <= 3,
            "live remount should reclaim old lowerdirs to a bounded count: {remount}"
        );

        lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        wait_for_command_stdout_contains(&lease, &command_id, "LIVE_REMOUNT_AFTER")?;
        wait_for_command_stdout_contains(&lease, &command_id, "public-live-5")?;

        let private_read =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": private_path}))?;
        ensure!(
            as_str(&private_read, "content")? == "live-private",
            "resumed command should write through the preserved private upperdir: {private_read}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_live_remount_repeated_cycles_keep_pinned_snapshot_and_private_state(
) -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_caller = format!("remount-repeat-public-{suffix}");
    let public_path = format!("compact-remount/repeat-public-{suffix}.bin");
    let state_path = format!("compact-remount/repeat-private-state-{suffix}.txt");
    let hash_prefix = format!("compact-remount/repeat-private-hash-{suffix}");

    let mut pinned_content = String::new();
    for index in 0..12 {
        let content = versioned_payload("repeat-pinned", index, 128 * 1024);
        if index == 11 {
            pinned_content.clone_from(&content);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_path,
                "content": content,
                "overwrite": true,
            }),
        )?;
    }
    let pinned_hash = sha256_hex(&[pinned_content.as_bytes()]);

    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command = format!(
        "bash -lc 'set -euo pipefail; printf REPEAT_REMOUNT_READY; for cycle in 1 2 3; do read -r _; actual=$(cat \"{workspace_root}/{public_path}\" | sha256sum); actual=${{actual%% *}}; test \"$actual\" = \"{pinned_hash}\"; printf \"%s:%s\\n\" \"$cycle\" \"$actual\" >> \"{workspace_root}/{state_path}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_prefix}-$cycle.sha256\"; printf \"REPEAT_REMOUNT_DONE_$cycle\"; done; sleep 30'"
    );
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 90,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable command for repeated remounts: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();

    let body = (|| -> Result<()> {
        wait_for_command_stdout_contains(&lease, &command_id, "REPEAT_REMOUNT_READY")?;
        let mut latest_public_content = pinned_content.clone();

        for cycle in 1..=3 {
            if cycle > 1 {
                latest_public_content =
                    versioned_payload("repeat-public-after-enter", cycle, 128 * 1024);
                call_ok_as(
                    &lease,
                    &public_caller,
                    catalog::SANDBOX_FILE_WRITE,
                    json!({
                        "path": &public_path,
                        "content": &latest_public_content,
                        "overwrite": true,
                    }),
                )?;
            }

            let remount = lease.call_ok(
                catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
                json!({
                    "probe_path": &public_path,
                    "probe_content": &pinned_content,
                }),
            )?;
            ensure!(
                remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                    && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                    && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
                "cycle {cycle} should verify live remount before lease retarget: {remount}"
            );
            assert_lowerdir_proof_fields(&remount)?;
            ensure!(
                as_i64(&remount, "active_leases_after")? == 1,
                "cycle {cycle} should keep the open isolated lease active: {remount}"
            );
            ensure!(
                as_i64(&remount, "process_count")? >= 1
                    && as_i64(&remount, "quiesced_process_count")?
                        == as_i64(&remount, "process_count")?,
                "cycle {cycle} should quiesce every process before switching: {remount}"
            );
            ensure!(
                as_i64(&remount, "pinned_cwd_count")? == 0
                    && as_i64(&remount, "pinned_fd_count")? == 0
                    && as_i64(&remount, "pinned_mapped_file_count")? == 0,
                "cycle {cycle} command should not pin the old workspace mount: {remount}"
            );
            if cycle == 1 {
                ensure!(
                    as_i64(&remount, "before_layer_dirs")? >= 12
                        && as_i64(&remount, "after_layer_dirs")? <= 3
                        && as_i64(&remount, "after_storage_bytes")?
                            < as_i64(&remount, "before_storage_bytes")?,
                    "first cycle should reclaim the deep pinned snapshot: {remount}"
                );
            }

            lease.call_ok(
                catalog::SANDBOX_COMMAND_WRITE_STDIN,
                json!({"command_id": command_id, "chars": format!("cycle-{cycle}\n"), "yield_time_ms": 1500}),
            )?;
            wait_for_command_stdout_contains(
                &lease,
                &command_id,
                &format!("REPEAT_REMOUNT_DONE_{cycle}"),
            )?;

            let hash_read = lease.call_ok(
                catalog::SANDBOX_FILE_READ,
                json!({"path": format!("{hash_prefix}-{cycle}.sha256")}),
            )?;
            ensure!(
                as_str(&hash_read, "content")? == pinned_hash,
                "cycle {cycle} resumed command should hash the pinned remounted snapshot: {hash_read}"
            );
            let isolated_read =
                lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &public_path}))?;
            ensure!(
                as_str(&isolated_read, "content")? == pinned_content,
                "cycle {cycle} isolated lease should keep the pinned snapshot despite public head movement: {isolated_read}"
            );
        }

        let state_read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &state_path}))?;
        let expected_state = format!("1:{pinned_hash}\n2:{pinned_hash}\n3:{pinned_hash}\n");
        ensure!(
            as_str(&state_read, "content")? == expected_state,
            "private upperdir state should accumulate across all remount cycles: {state_read}"
        );

        let public_read = call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_path}),
        )?;
        ensure!(
            as_str(&public_read, "content")? == latest_public_content,
            "public caller should see the moved public head while isolated caller stays pinned: {public_read}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_live_remount_preserves_many_file_tree_integrity() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_caller = format!("remount-many-public-{suffix}");
    let tree_root = format!("compact-remount/many-tree-{suffix}");
    let manifest_path = format!("{tree_root}/manifest.txt");
    let hash_path = format!("{tree_root}/private-tree-hash.sha256");
    let hash_tmp = format!("{tree_root}/private-tree-hash.tmp");
    let file_count = 32usize;
    let rewrite_count = 3usize;
    let payload_bytes = 16 * 1024usize;
    let mut manifest = String::new();
    let mut paths = Vec::with_capacity(file_count);
    let mut final_contents = vec![String::new(); file_count];

    for index in 0..file_count {
        let path = format!("{tree_root}/dir-{}/leaf-{index:03}.bin", index % 8);
        manifest.push_str(&path);
        manifest.push('\n');
        paths.push(path);
    }

    for revision in 0..rewrite_count {
        for (index, path) in paths.iter().enumerate() {
            let content = versioned_payload(
                &format!("many-file-pinned-r{revision}"),
                index,
                payload_bytes,
            );
            if revision + 1 == rewrite_count {
                final_contents[index].clone_from(&content);
            }
            call_ok_as(
                &lease,
                &public_caller,
                catalog::SANDBOX_FILE_WRITE,
                json!({
                    "path": path,
                    "content": &content,
                    "overwrite": true,
                }),
            )?;
        }
    }
    call_ok_as(
        &lease,
        &public_caller,
        catalog::SANDBOX_FILE_WRITE,
        json!({
            "path": &manifest_path,
            "content": &manifest,
            "overwrite": true,
        }),
    )?;
    let mut expected_hasher = Sha256::new();
    for content in &final_contents {
        expected_hasher.update(content.as_bytes());
    }
    let expected_hash = format!("{:x}", expected_hasher.finalize());
    let probe_index = file_count - 1;
    let probe_path = paths[probe_index].clone();
    let probe_content = final_contents[probe_index].clone();
    let overwritten_path = paths[0].clone();
    let overwritten_original = final_contents[0].clone();
    let overwritten_public = versioned_payload(
        "many-file-public-after-remount",
        file_count + 1,
        payload_bytes,
    );

    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command = format!(
        "bash -lc 'set -euo pipefail; printf MANY_TREE_READY; read -r _; actual=$(while IFS= read -r p; do cat \"{workspace_root}/$p\"; done < \"{workspace_root}/{manifest_path}\" | sha256sum); actual=${{actual%% *}}; test \"$actual\" = \"{expected_hash}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_tmp}\"; mv \"{workspace_root}/{hash_tmp}\" \"{workspace_root}/{hash_path}\"; printf MANY_TREE_DONE; sleep 30'"
    );
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 90,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable many-file command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();

    let body = (|| -> Result<()> {
        wait_for_command_stdout_contains(&lease, &command_id, "MANY_TREE_READY")?;

        let remount = lease.call_ok(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({
                "probe_path": &probe_path,
                "probe_content": &probe_content,
            }),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "many-file remount should verify mount switch before lease retarget: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "before_layer_dirs")? >= (file_count * rewrite_count) as i64
                && as_i64(&remount, "after_layer_dirs")? <= 3,
            "many-file remount should reclaim a wide retained tree to bounded dirs: {remount}"
        );
        ensure!(
            as_i64(&remount, "after_storage_bytes")? < as_i64(&remount, "before_storage_bytes")?,
            "many-file remount should reduce retained storage while the lease remains open: {remount}"
        );
        ensure!(
            as_i64(&remount, "process_count")? >= 1
                && as_i64(&remount, "quiesced_process_count")?
                    == as_i64(&remount, "process_count")?,
            "many-file remount should quiesce the full command process group: {remount}"
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "many-file command should not pin the old workspace mount: {remount}"
        );

        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &overwritten_path,
                "content": &overwritten_public,
                "overwrite": true,
            }),
        )?;

        lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        wait_for_command_stdout_contains(&lease, &command_id, "MANY_TREE_DONE")?;

        let hash_read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &hash_path}))?;
        ensure!(
            as_str(&hash_read, "content")? == expected_hash,
            "resumed command should hash the original pinned many-file tree after remount: {hash_read}"
        );
        let isolated_read = lease.call_ok(
            catalog::SANDBOX_FILE_READ,
            json!({"path": &overwritten_path}),
        )?;
        ensure!(
            as_str(&isolated_read, "content")? == overwritten_original,
            "isolated lease should not observe public head movement after remount: {isolated_read}"
        );
        let public_read = call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &overwritten_path}),
        )?;
        ensure!(
            as_str(&public_read, "content")? == overwritten_public,
            "public caller should see the post-remount head update: {public_read}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_live_remount_preserves_concurrent_pip_style_install_tree() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_caller = format!("remount-pip-public-{suffix}");
    let public_path = format!("compact-remount/pip-public-{suffix}.bin");
    let install_root = format!("compact-remount/pip-style-site-packages-{suffix}");
    let tree_hash_path = format!("{install_root}/TREE.sha256");
    let post_hash_path = format!("{install_root}/POST_REMOUNT.sha256");
    let record_path = format!("{install_root}/pip_style_demo-0.0.0.dist-info/RECORD");
    let sample_module_path = format!("{install_root}/pkg_11/module_123.py");
    let sample_resource_path = format!("{install_root}/pkg_15/data/resource_255.txt");

    let mut pinned_content = String::new();
    for index in 0..18 {
        let content = versioned_payload("pip-style-public", index, 96 * 1024);
        if index == 17 {
            pinned_content.clone_from(&content);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({"path": &public_path, "content": &content, "overwrite": true}),
        )?;
    }
    let public_after_remount = versioned_payload("pip-style-public-after-remount", 0, 96 * 1024);

    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command = format!(
        r#"bash -lc 'set -euo pipefail; python3 - <<"PY_INSTALL"
import concurrent.futures
import hashlib
import os
import pathlib

root = pathlib.Path("{workspace_root}") / "{install_root}"
root.mkdir(parents=True, exist_ok=True)

def write_pair(index):
    package = root / ("pkg_%02d" % (index % 16))
    data_dir = package / "data"
    package.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    module_path = package / ("module_%03d.py" % index)
    resource_path = data_dir / ("resource_%03d.txt" % index)
    module_payload = "VALUE = %d\nLABEL = \"%s\"\n" % (index, "x" * 128)
    resource_payload = ("resource-%03d\n" % index) + ("R" * 512)
    module_path.write_text(module_payload, encoding="utf-8")
    resource_path.write_text(resource_payload, encoding="utf-8")

with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
    list(executor.map(write_pair, range(384)))

for package_index in range(16):
    package = root / ("pkg_%02d" % package_index)
    (package / "__init__.py").write_text("PACKAGE_INDEX = %d\n" % package_index, encoding="utf-8")

dist = root / "pip_style_demo-0.0.0.dist-info"
dist.mkdir(parents=True, exist_ok=True)
(dist / "METADATA").write_text("Name: pip-style-demo\nVersion: 0.0.0\n", encoding="utf-8")
(dist / "WHEEL").write_text("Wheel-Version: 1.0\nRoot-Is-Purelib: true\n", encoding="utf-8")
records = [str(path.relative_to(root)) for path in sorted(root.rglob("*")) if path.is_file()]
(dist / "RECORD").write_text("\n".join(records) + "\n", encoding="utf-8")

def tree_hash():
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in ("TREE.sha256", "POST_REMOUNT.sha256"):
            continue
        h.update(str(path.relative_to(root)).encode("utf-8"))
        h.update(bytes([0]))
        h.update(path.read_bytes())
        h.update(bytes([0]))
    return h.hexdigest()

original = tree_hash()
(root / "TREE.sha256").write_text(original, encoding="utf-8")
print("PIP_STYLE_READY", flush=True)
PY_INSTALL
read -r _
python3 - <<"PY_VERIFY"
import hashlib
import pathlib
import time

root = pathlib.Path("{workspace_root}") / "{install_root}"

def tree_hash():
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in ("TREE.sha256", "POST_REMOUNT.sha256"):
            continue
        h.update(str(path.relative_to(root)).encode("utf-8"))
        h.update(bytes([0]))
        h.update(path.read_bytes())
        h.update(bytes([0]))
    return h.hexdigest()

expected = (root / "TREE.sha256").read_text(encoding="utf-8")
after = tree_hash()
if after != expected:
    raise SystemExit("pip style tree changed after remount " + after)
(root / "POST_REMOUNT.sha256").write_text(after, encoding="utf-8")
print("PIP_STYLE_DONE", flush=True)
time.sleep(30)
PY_VERIFY'"#
    );
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 180,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable pip-style install command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();

    let body = (|| -> Result<()> {
        wait_for_command_stdout_contains(&lease, &command_id, "PIP_STYLE_READY")?;

        let tree_hash_read =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &tree_hash_path}))?;
        let expected_tree_hash = as_str(&tree_hash_read, "content")?.to_owned();
        ensure!(
            expected_tree_hash.len() == 64,
            "pip-style tree hash should be a sha256 hex digest: {tree_hash_read}"
        );
        let record_read =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &record_path}))?;
        ensure!(
            as_str(&record_read, "content")?.lines().count() >= 700,
            "pip-style RECORD should cover hundreds of installed files: {record_read}"
        );

        let remount = lease.call_ok(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({"probe_path": &public_path, "probe_content": &pinned_content}),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "pip-style install remount should verify mount switch before lease retarget: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "remountable_commands")? == 1
                && as_i64(&remount, "process_count")? >= 1
                && as_i64(&remount, "quiesced_process_count")?
                    == as_i64(&remount, "process_count")?,
            "pip-style remount should quiesce the install command process group: {remount}"
        );
        ensure!(
            as_i64(&remount, "after_layer_dirs")? <= 3
                && as_i64(&remount, "after_layer_dirs")? < as_i64(&remount, "before_layer_dirs")?,
            "pip-style remount should compact the retained lower chain to bounded dirs: {remount}"
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "completed pip-style install should not pin the old workspace mount while waiting: {remount}"
        );

        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_path,
                "content": &public_after_remount,
                "overwrite": true,
            }),
        )?;
        lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        wait_for_command_stdout_contains(&lease, &command_id, "PIP_STYLE_DONE")?;

        let post_hash_read =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &post_hash_path}))?;
        ensure!(
            as_str(&post_hash_read, "content")? == expected_tree_hash,
            "resumed pip-style command should verify the private install tree after remount: {post_hash_read}"
        );
        let module_read = lease.call_ok(
            catalog::SANDBOX_FILE_READ,
            json!({"path": &sample_module_path}),
        )?;
        ensure!(
            as_str(&module_read, "content")?.contains("VALUE = 123"),
            "sample installed module should survive remount: {module_read}"
        );
        let resource_read = lease.call_ok(
            catalog::SANDBOX_FILE_READ,
            json!({"path": &sample_resource_path}),
        )?;
        ensure!(
            as_str(&resource_read, "content")?.starts_with("resource-255\n"),
            "sample installed resource should survive remount: {resource_read}"
        );
        let isolated_public =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &public_path}))?;
        ensure!(
            as_str(&isolated_public, "content")? == pinned_content,
            "pip-style isolated lease should keep its original public snapshot after remount: {isolated_public}"
        );
        let public_read = call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_path}),
        )?;
        ensure!(
            as_str(&public_read, "content")? == public_after_remount,
            "public caller should see the post-remount public head: {public_read}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_live_remount_coverage_goal4_hard_concurrent_real_pip_install_tree() -> Result<()>
{
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let caller = format!("remount-real-pip-{suffix}");
    let public_caller = format!("remount-real-pip-public-{suffix}");
    let public_path = format!("compact-remount/real-pip-public-{suffix}.bin");
    let install_root = format!("compact-remount/real-pip-install-{suffix}");
    let tree_hash_path = format!("{install_root}/TREE.sha256");
    let post_hash_path = format!("{install_root}/POST_REMOUNT.sha256");
    let file_count_path = format!("{install_root}/INSTALLED_FILE_COUNT.txt");
    let sample_module_path = format!("{install_root}/site-a/real_pip_alpha/module_123.py");
    let sample_resource_path = format!("{install_root}/site-b/real_pip_beta/data/resource_127.txt");

    let mut pinned_content = String::new();
    for index in 0..18 {
        let content = versioned_payload("real-pip-public", index, 96 * 1024);
        if index == 17 {
            pinned_content.clone_from(&content);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({"path": &public_path, "content": &content, "overwrite": true}),
        )?;
    }
    let public_after_remount = versioned_payload("real-pip-public-after-remount", 0, 96 * 1024);

    let enter = call_ok_as(&lease, &caller, catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command = format!(
        r#"bash -lc 'set -euo pipefail; python3 - <<"PY_INSTALL"
import concurrent.futures
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys

root = pathlib.Path("{workspace_root}") / "{install_root}"
src_root = root / "src"
target_a = root / "site-a"
target_b = root / "site-b"
for path in (src_root, target_a, target_b):
    path.mkdir(parents=True, exist_ok=True)

def write_package(project_name, module_name, package_index):
    project = src_root / project_name
    package = project / module_name
    data_dir = package / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(
        "PACKAGE_NAME = %r\nPACKAGE_INDEX = %d\n" % (module_name, package_index),
        encoding="utf-8",
    )
    for index in range(128):
        (package / ("module_%03d.py" % index)).write_text(
            "VALUE = %d\nPACKAGE_NAME = %r\n" % (index, module_name),
            encoding="utf-8",
        )
        (data_dir / ("resource_%03d.txt" % index)).write_text(
            ("resource-%03d-%s\n" % (index, module_name)) + ("R" * 768),
            encoding="utf-8",
        )
    setup_py = (
        "from setuptools import setup, find_packages\n"
        "setup(name=%r, version=\"0.0.%d\", packages=find_packages(), "
        "include_package_data=True, package_data=dict([(%r, [\"data/*.txt\"])]))\n"
    ) % (project_name, package_index, module_name)
    (project / "setup.py").write_text(setup_py, encoding="utf-8")

write_package("real-pip-alpha", "real_pip_alpha", 1)
write_package("real-pip-beta", "real_pip_beta", 2)

def run_pip(project_name, target):
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-build-isolation",
            "--disable-pip-version-check",
            "--target",
            str(target),
            str(src_root / project_name),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit("pip install failed for " + project_name + "\n" + proc.stdout)

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
    futures = [
        executor.submit(run_pip, "real-pip-alpha", target_a),
        executor.submit(run_pip, "real-pip-beta", target_b),
    ]
    for future in futures:
        future.result()

installed_roots = [target_a, target_b]

def installed_files():
    files = []
    for base in installed_roots:
        files.extend(path for path in base.rglob("*") if path.is_file())
    return sorted(files)

def tree_hash():
    h = hashlib.sha256()
    for path in installed_files():
        h.update(str(path.relative_to(root)).encode("utf-8"))
        h.update(bytes([0]))
        h.update(path.read_bytes())
        h.update(bytes([0]))
    return h.hexdigest()

env = dict(os.environ)
env["PYTHONPATH"] = str(target_a) + os.pathsep + str(target_b)
import_check = subprocess.check_output(
    [
        sys.executable,
        "-c",
        "import real_pip_alpha, real_pip_beta\nprint(real_pip_alpha.PACKAGE_NAME + \":\" + real_pip_beta.PACKAGE_NAME)",
    ],
    env=env,
    text=True,
).strip()
if import_check != "real_pip_alpha:real_pip_beta":
    raise SystemExit("unexpected import result " + import_check)

files = installed_files()
if len(files) < 500:
    raise SystemExit("pip install produced too few files: " + str(len(files)))
(root / "INSTALLED_FILE_COUNT.txt").write_text(str(len(files)), encoding="utf-8")
(root / "TREE.sha256").write_text(tree_hash(), encoding="utf-8")
print("REAL_PIP_READY", flush=True)
PY_INSTALL
read -r _
python3 - <<"PY_VERIFY"
import hashlib
import os
import pathlib
import subprocess
import sys

root = pathlib.Path("{workspace_root}") / "{install_root}"
target_a = root / "site-a"
target_b = root / "site-b"
installed_roots = [target_a, target_b]

def installed_files():
    files = []
    for base in installed_roots:
        files.extend(path for path in base.rglob("*") if path.is_file())
    return sorted(files)

def tree_hash():
    h = hashlib.sha256()
    for path in installed_files():
        h.update(str(path.relative_to(root)).encode("utf-8"))
        h.update(bytes([0]))
        h.update(path.read_bytes())
        h.update(bytes([0]))
    return h.hexdigest()

expected = (root / "TREE.sha256").read_text(encoding="utf-8")
after = tree_hash()
if after != expected:
    raise SystemExit("real pip install tree changed after remount " + after)

env = dict(os.environ)
env["PYTHONPATH"] = str(target_a) + os.pathsep + str(target_b)
import_check = subprocess.check_output(
    [
        sys.executable,
        "-c",
        "import real_pip_alpha, real_pip_beta\nprint(real_pip_alpha.PACKAGE_NAME + \":\" + real_pip_beta.PACKAGE_NAME)",
    ],
    env=env,
    text=True,
).strip()
if import_check != "real_pip_alpha:real_pip_beta":
    raise SystemExit("unexpected post-remount import result " + import_check)

(root / "POST_REMOUNT.sha256").write_text(after, encoding="utf-8")
print("REAL_PIP_DONE", flush=True)
PY_VERIFY
sleep 30'"#
    );
    let command_started_at = std::time::Instant::now();
    let started = call_ok_as(
        &lease,
        &caller,
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 240,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable real pip install command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();

    let body = (|| -> Result<()> {
        wait_for_command_stdout_contains_as_timeout(
            &lease,
            &caller,
            &command_id,
            "REAL_PIP_READY",
            std::time::Duration::from_secs(60),
        )?;
        let install_ready_ms = command_started_at.elapsed().as_millis();

        let tree_hash_read = call_ok_as(
            &lease,
            &caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &tree_hash_path}),
        )?;
        let expected_tree_hash = as_str(&tree_hash_read, "content")?.to_owned();
        ensure!(
            expected_tree_hash.len() == 64,
            "real pip tree hash should be a sha256 hex digest: {tree_hash_read}"
        );
        let file_count_read = call_ok_as(
            &lease,
            &caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &file_count_path}),
        )?;
        let installed_file_count = as_str(&file_count_read, "content")?
            .trim()
            .parse::<i64>()
            .with_context(|| format!("parse real pip installed file count: {file_count_read}"))?;
        ensure!(
            installed_file_count >= 500,
            "real pip install should create hundreds of installed files: {file_count_read}"
        );

        let remount_started_at = std::time::Instant::now();
        let remount = call_ok_as(
            &lease,
            &caller,
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({"probe_path": &public_path, "probe_content": &pinned_content}),
        )?;
        let remount_ms = remount_started_at.elapsed().as_millis();
        let before_storage_bytes = as_i64(&remount, "before_storage_bytes")?;
        let after_storage_bytes = as_i64(&remount, "after_storage_bytes")?;
        let saved_storage_bytes = before_storage_bytes.saturating_sub(after_storage_bytes);
        let storage_reduction_pct = if before_storage_bytes > 0 {
            (saved_storage_bytes as f64 / before_storage_bytes as f64) * 100.0
        } else {
            0.0
        };
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "real pip install remount should verify mount switch before lease retarget: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "remountable_commands")? == 1
                && as_i64(&remount, "process_count")? >= 1
                && as_i64(&remount, "quiesced_process_count")?
                    == as_i64(&remount, "process_count")?,
            "real pip remount should quiesce the command process group: {remount}"
        );
        ensure!(
            as_i64(&remount, "after_layer_dirs")? <= 3
                && as_i64(&remount, "after_layer_dirs")? < as_i64(&remount, "before_layer_dirs")?,
            "real pip remount should compact the retained lower chain to bounded dirs: {remount}"
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "completed real pip install should not pin the old workspace mount while waiting: {remount}"
        );

        let post_verify_started_at = std::time::Instant::now();
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_path,
                "content": &public_after_remount,
                "overwrite": true,
            }),
        )?;
        call_ok_as(
            &lease,
            &caller,
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        wait_for_command_stdout_contains_as_timeout(
            &lease,
            &caller,
            &command_id,
            "REAL_PIP_DONE",
            std::time::Duration::from_secs(30),
        )?;
        let post_verify_ms = post_verify_started_at.elapsed().as_millis();

        let post_hash_read = call_ok_as(
            &lease,
            &caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &post_hash_path}),
        )?;
        ensure!(
            as_str(&post_hash_read, "content")? == expected_tree_hash,
            "resumed real pip command should verify installed tree after remount: {post_hash_read}"
        );
        let module_read = call_ok_as(
            &lease,
            &caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &sample_module_path}),
        )?;
        ensure!(
            as_str(&module_read, "content")?.contains("VALUE = 123"),
            "sample real pip installed module should survive remount: {module_read}"
        );
        let resource_read = call_ok_as(
            &lease,
            &caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &sample_resource_path}),
        )?;
        ensure!(
            as_str(&resource_read, "content")?.starts_with("resource-127-real_pip_beta\n"),
            "sample real pip installed package data should survive remount: {resource_read}"
        );
        let isolated_public = call_ok_as(
            &lease,
            &caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_path}),
        )?;
        ensure!(
            as_str(&isolated_public, "content")? == pinned_content,
            "real pip isolated lease should keep its original public snapshot after remount: {isolated_public}"
        );
        let public_read = call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_path}),
        )?;
        ensure!(
            as_str(&public_read, "content")? == public_after_remount,
            "public caller should see the post-remount public head: {public_read}"
        );
        println!(
            "REAL_PIP_SPACE_TIME_BENCH installed_files={installed_file_count} \
             install_ready_ms={install_ready_ms} remount_ms={remount_ms} \
             post_verify_ms={post_verify_ms} before_storage_bytes={before_storage_bytes} \
             after_storage_bytes={after_storage_bytes} saved_storage_bytes={saved_storage_bytes} \
             storage_reduction_pct={storage_reduction_pct:.2} before_layer_dirs={} \
             after_layer_dirs={} before_manifest_depth={} after_manifest_depth={} \
             compacted_snapshot_layers={} remounted_layer_count={} process_count={} \
             quiesced_process_count={}",
            as_i64(&remount, "before_layer_dirs")?,
            as_i64(&remount, "after_layer_dirs")?,
            as_i64(&remount, "before_manifest_depth")?,
            as_i64(&remount, "after_manifest_depth")?,
            as_i64(&remount, "compacted_snapshot_layers")?,
            as_i64(&remount, "remounted_layer_count")?,
            as_i64(&remount, "process_count")?,
            as_i64(&remount, "quiesced_process_count")?,
        );
        Ok(())
    })();

    let _ = call_ok_as(
        &lease,
        &caller,
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = call_ok_as(
        &lease,
        &caller,
        catalog::SANDBOX_ISOLATION_EXIT,
        json!({"grace_s": 0.1}),
    );
    body
}

#[test]
fn compact_remount_live_remount_preserves_complex_command_integrity() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_a = format!("compact-remount/complex-a-{suffix}.bin");
    let public_b = format!("compact-remount/complex-b-{suffix}.bin");
    let hash_path = format!("compact-remount/complex-private-hash-{suffix}.txt");
    let hash_tmp = format!("compact-remount/complex-private-hash-{suffix}.tmp");

    let final_a = format!("a-final-8\n{}", "A".repeat(64 * 1024));
    let final_b = format!("b-final-9\n{}", "B".repeat(64 * 1024));
    for index in 0..10 {
        let (path, content) = if index % 2 == 0 {
            (
                public_a.as_str(),
                format!("a-final-{index}\n{}", "A".repeat(64 * 1024)),
            )
        } else {
            (
                public_b.as_str(),
                format!("b-final-{index}\n{}", "B".repeat(64 * 1024)),
            )
        };
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": path,
                "content": content,
                "overwrite": true,
            }),
        )?;
    }
    let expected_hash = sha256_hex(&[final_a.as_bytes(), final_b.as_bytes()]);

    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command = format!(
        "bash -lc 'set -euo pipefail; printf COMPLEX_REMOUNT_READY; read -r _; actual=$(cat \"{workspace_root}/{public_a}\" \"{workspace_root}/{public_b}\" | sha256sum); actual=${{actual%% *}}; test \"$actual\" = \"{expected_hash}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_tmp}\"; mv \"{workspace_root}/{hash_tmp}\" \"{workspace_root}/{hash_path}\"; printf COMPLEX_REMOUNT_AFTER; sleep 30'"
    );
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable integrity command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();
    wait_for_command_stdout_contains(&lease, &command_id, "COMPLEX_REMOUNT_READY")?;

    let body = (|| -> Result<()> {
        let remount = lease.call_ok(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({
                "probe_path": &public_a,
                "probe_content": &final_a,
            }),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "integrity command remount should be verified before lease retarget: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "after_layer_dirs")? <= 3,
            "integrity remount should reclaim the lower chain to bounded dirs: {remount}"
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "integrity command should not pin the old workspace mount: {remount}"
        );

        lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        wait_for_command_stdout_contains(&lease, &command_id, "COMPLEX_REMOUNT_AFTER")?;

        let hash_read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": hash_path}))?;
        ensure!(
            as_str(&hash_read, "content")? == expected_hash,
            "resumed command should observe a consistent multi-file snapshot and atomically write the integrity hash: {hash_read}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_live_remount_preserves_process_tree_and_private_state() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_a = format!("compact-remount/tree-a-{suffix}.bin");
    let public_b = format!("compact-remount/tree-b-{suffix}.bin");
    let public_c = format!("compact-remount/tree-c-{suffix}.bin");
    let private_state = format!("compact-remount/tree-private-state-{suffix}.txt");
    let hash_path = format!("compact-remount/tree-private-hash-{suffix}.sha256");
    let hash_tmp = format!("compact-remount/tree-private-hash-{suffix}.tmp");

    let final_a = format!("tree-a-final-15\n{}", "A".repeat(256 * 1024));
    let final_b = format!("tree-b-final-16\n{}", "B".repeat(256 * 1024));
    let final_c = format!("tree-c-final-17\n{}", "C".repeat(256 * 1024));
    for index in 0..18 {
        let (path, content) = match index % 3 {
            0 => (
                public_a.as_str(),
                format!("tree-a-final-{index}\n{}", "A".repeat(256 * 1024)),
            ),
            1 => (
                public_b.as_str(),
                format!("tree-b-final-{index}\n{}", "B".repeat(256 * 1024)),
            ),
            _ => (
                public_c.as_str(),
                format!("tree-c-final-{index}\n{}", "C".repeat(256 * 1024)),
            ),
        };
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": path,
                "content": content,
                "overwrite": true,
            }),
        )?;
    }
    let private_content = format!("private-state-before-remount-{suffix}\n");
    let expected_hash = sha256_hex(&[
        final_a.as_bytes(),
        final_b.as_bytes(),
        final_c.as_bytes(),
        private_content.as_bytes(),
    ]);

    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command = format!(
        "bash -lc 'set -euo pipefail; printf \"%s\" \"{private_content}\" > \"{workspace_root}/{private_state}\"; tail -f /dev/null >/tmp/remount-tree-child.log 2>&1 & child=$!; printf TREE_REMOUNT_READY; read -r _; actual=$(cat \"{workspace_root}/{public_a}\" \"{workspace_root}/{public_b}\" \"{workspace_root}/{public_c}\" \"{workspace_root}/{private_state}\" | sha256sum); actual=${{actual%% *}}; test \"$actual\" = \"{expected_hash}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_tmp}\"; mv \"{workspace_root}/{hash_tmp}\" \"{workspace_root}/{hash_path}\"; kill \"$child\"; wait \"$child\" 2>/dev/null || true; printf TREE_REMOUNT_DONE; sleep 30'"
    );
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable process-tree command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();
    wait_for_command_stdout_contains(&lease, &command_id, "TREE_REMOUNT_READY")?;

    let body = (|| -> Result<()> {
        let private_read_before =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &private_state}))?;
        ensure!(
            as_str(&private_read_before, "content")? == private_content,
            "command-created private state should be visible before remount: {private_read_before}"
        );

        let remount = lease.call_ok(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({
                "probe_path": &public_c,
                "probe_content": &final_c,
            }),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "process-tree remount should be verified before lease retarget: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "process_count")? >= 2
                && as_i64(&remount, "quiesced_process_count")?
                    == as_i64(&remount, "process_count")?,
            "process-tree remount should quiesce the shell and background child: {remount}"
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "process-tree remount should not pin the old workspace mount: {remount}"
        );
        ensure!(
            as_i64(&remount, "before_layer_dirs")? >= 18
                && as_i64(&remount, "after_layer_dirs")? <= 3,
            "process-tree remount should reclaim a deep public chain: {remount}"
        );

        lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        wait_for_command_stdout_contains(&lease, &command_id, "TREE_REMOUNT_DONE")?;

        let private_read_after =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &private_state}))?;
        ensure!(
            as_str(&private_read_after, "content")? == private_content,
            "remount should preserve private upperdir state created by the running command: {private_read_after}"
        );
        let hash_read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": hash_path}))?;
        ensure!(
            as_str(&hash_read, "content")? == expected_hash,
            "resumed process tree should verify public snapshot plus preserved private state: {hash_read}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_live_remount_quiesces_process_fanout_and_preserves_integrity() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_caller = format!("remount-fanout-public-{suffix}");
    let public_a = format!("compact-remount/fanout-a-{suffix}.bin");
    let public_b = format!("compact-remount/fanout-b-{suffix}.bin");
    let private_state = format!("compact-remount/fanout-private-state-{suffix}.txt");
    let hash_path = format!("compact-remount/fanout-private-hash-{suffix}.sha256");
    let hash_tmp = format!("compact-remount/fanout-private-hash-{suffix}.tmp");

    let mut final_a = String::new();
    let mut final_b = String::new();
    for index in 0..24 {
        let (path, label) = if index % 2 == 0 {
            (&public_a, "fanout-a")
        } else {
            (&public_b, "fanout-b")
        };
        let content = versioned_payload(label, index, 192 * 1024);
        if index == 22 {
            final_a.clone_from(&content);
        } else if index == 23 {
            final_b.clone_from(&content);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": path,
                "content": content,
                "overwrite": true,
            }),
        )?;
    }
    let private_content = format!("fanout-private-state-before-remount-{suffix}\n");
    let expected_hash = sha256_hex(&[
        final_a.as_bytes(),
        final_b.as_bytes(),
        private_content.as_bytes(),
    ]);
    let public_head_after_remount = versioned_payload("fanout-public-after-remount", 0, 192 * 1024);

    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command = format!(
        "bash -lc 'set -euo pipefail; printf \"%s\" \"{private_content}\" > \"{workspace_root}/{private_state}\"; pids=\"\"; for i in $(seq 1 10); do (while true; do sleep 5; done) & pids=\"$pids $!\"; done; printf FANOUT_REMOUNT_READY; read -r _; actual=$(cat \"{workspace_root}/{public_a}\" \"{workspace_root}/{public_b}\" \"{workspace_root}/{private_state}\" | sha256sum); actual=${{actual%% *}}; test \"$actual\" = \"{expected_hash}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_tmp}\"; mv \"{workspace_root}/{hash_tmp}\" \"{workspace_root}/{hash_path}\"; for pid in $pids; do kill \"$pid\" 2>/dev/null || true; done; wait $pids 2>/dev/null || true; printf FANOUT_REMOUNT_DONE; sleep 30'"
    );
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 90,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable fanout command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();
    wait_for_command_stdout_contains(&lease, &command_id, "FANOUT_REMOUNT_READY")?;

    let body = (|| -> Result<()> {
        let private_read_before =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &private_state}))?;
        ensure!(
            as_str(&private_read_before, "content")? == private_content,
            "command-created fanout private state should exist before remount: {private_read_before}"
        );

        let remount = lease.call_ok(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({
                "probe_path": &public_b,
                "probe_content": &final_b,
            }),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "fanout remount should be verified before lease retarget: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "process_count")? >= 10
                && as_i64(&remount, "quiesced_process_count")?
                    == as_i64(&remount, "process_count")?,
            "fanout remount should quiesce every child process: {remount}"
        );
        ensure!(
            remount.get("process_resumed").and_then(Value::as_bool) == Some(true),
            "fanout remount should resume the process group before returning: {remount}"
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "fanout command should not pin the old workspace mount: {remount}"
        );
        ensure!(
            as_i64(&remount, "before_layer_dirs")? >= 24
                && as_i64(&remount, "after_layer_dirs")? <= 3
                && as_i64(&remount, "after_storage_bytes")?
                    < as_i64(&remount, "before_storage_bytes")?,
            "fanout remount should reclaim the deep public chain: {remount}"
        );

        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_a,
                "content": &public_head_after_remount,
                "overwrite": true,
            }),
        )?;

        lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        wait_for_command_stdout_contains(&lease, &command_id, "FANOUT_REMOUNT_DONE")?;

        let hash_read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &hash_path}))?;
        ensure!(
            as_str(&hash_read, "content")? == expected_hash,
            "resumed fanout command should hash the pinned snapshot plus private state: {hash_read}"
        );
        let live_read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &public_a}))?;
        ensure!(
            as_str(&live_read, "content")? == final_a,
            "remounted fanout lease should not observe public head movement: {live_read}"
        );
        let public_read = call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_a}),
        )?;
        ensure!(
            as_str(&public_read, "content")? == public_head_after_remount,
            "public caller should observe the post-remount fanout head update: {public_read}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_live_remounts_multiple_remountable_commands_consistently() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_a = format!("compact-remount/multi-a-{suffix}.bin");
    let public_b = format!("compact-remount/multi-b-{suffix}.bin");
    let hash_a = format!("compact-remount/multi-private-a-{suffix}.sha256");
    let hash_a_tmp = format!("compact-remount/multi-private-a-{suffix}.tmp");
    let hash_b = format!("compact-remount/multi-private-b-{suffix}.sha256");
    let hash_b_tmp = format!("compact-remount/multi-private-b-{suffix}.tmp");

    let final_a = format!("multi-a-final-10\n{}", "A".repeat(96 * 1024));
    let final_b = format!("multi-b-final-11\n{}", "B".repeat(96 * 1024));
    for index in 0..12 {
        let (path, content) = if index % 2 == 0 {
            (
                public_a.as_str(),
                format!("multi-a-final-{index}\n{}", "A".repeat(96 * 1024)),
            )
        } else {
            (
                public_b.as_str(),
                format!("multi-b-final-{index}\n{}", "B".repeat(96 * 1024)),
            )
        };
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": path,
                "content": content,
                "overwrite": true,
            }),
        )?;
    }
    let expected_ab = sha256_hex(&[final_a.as_bytes(), final_b.as_bytes()]);
    let expected_b = sha256_hex(&[final_b.as_bytes()]);

    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command_a = format!(
        "bash -lc 'set -euo pipefail; printf MULTI_REMOUNT_A_READY; read -r _; actual=$(cat \"{workspace_root}/{public_a}\" \"{workspace_root}/{public_b}\" | sha256sum); actual=${{actual%% *}}; test \"$actual\" = \"{expected_ab}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_a_tmp}\"; mv \"{workspace_root}/{hash_a_tmp}\" \"{workspace_root}/{hash_a}\"; printf MULTI_REMOUNT_A_DONE; sleep 30'"
    );
    let command_b = format!(
        "bash -lc 'set -euo pipefail; printf MULTI_REMOUNT_B_READY; read -r _; actual=$( (cat \"{workspace_root}/{public_b}\" | sha256sum) ); actual=${{actual%% *}}; test \"$actual\" = \"{expected_b}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_b_tmp}\"; mv \"{workspace_root}/{hash_b_tmp}\" \"{workspace_root}/{hash_b}\"; printf MULTI_REMOUNT_B_DONE; sleep 30'"
    );

    let started_a = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command_a,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    let started_b = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command_b,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    ensure!(
        as_str(&started_a, "status")? == "running" && as_str(&started_b, "status")? == "running",
        "test requires two live remountable isolated commands: {started_a} {started_b}"
    );
    let command_a_id = as_str(&started_a, "command_id")?.to_owned();
    let command_b_id = as_str(&started_b, "command_id")?.to_owned();
    wait_for_command_stdout_contains(&lease, &command_a_id, "MULTI_REMOUNT_A_READY")?;
    wait_for_command_stdout_contains(&lease, &command_b_id, "MULTI_REMOUNT_B_READY")?;

    let body = (|| -> Result<()> {
        let remount = lease.call_ok(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({
                "probe_path": &public_b,
                "probe_content": &final_b,
            }),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "multi-command remount should be verified before lease retarget: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "remountable_commands")? == 2,
            "remount should account for both opted-in commands: {remount}"
        );
        ensure!(
            as_i64(&remount, "process_count")? >= 2
                && as_i64(&remount, "quiesced_process_count")?
                    == as_i64(&remount, "process_count")?,
            "remount should quiesce every process in both command groups: {remount}"
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "multi-command remount should not pin the old workspace mount: {remount}"
        );
        ensure!(
            as_i64(&remount, "before_layer_dirs")? >= 12
                && as_i64(&remount, "after_layer_dirs")? <= 3,
            "multi-command remount should reclaim the lower chain to bounded dirs: {remount}"
        );

        lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_a_id, "chars": "go\n", "yield_time_ms": 1000}),
        )?;
        lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_b_id, "chars": "go\n", "yield_time_ms": 1000}),
        )?;
        wait_for_command_stdout_contains(&lease, &command_a_id, "MULTI_REMOUNT_A_DONE")?;
        wait_for_command_stdout_contains(&lease, &command_b_id, "MULTI_REMOUNT_B_DONE")?;

        let hash_a_read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": hash_a}))?;
        ensure!(
            as_str(&hash_a_read, "content")? == expected_ab,
            "command A should read a consistent two-file snapshot after remount: {hash_a_read}"
        );
        let hash_b_read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": hash_b}))?;
        ensure!(
            as_str(&hash_b_read, "content")? == expected_b,
            "command B child pipeline should read the remounted workspace after resume: {hash_b_read}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_a_id}),
    );
    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_b_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_live_remount_with_older_open_lease_preserves_both_snapshots() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let old_caller = format!("remount-old-lease-{suffix}");
    let live_caller = format!("remount-live-lease-{suffix}");
    let public_caller = format!("remount-public-writer-{suffix}");
    let public_path = format!("compact-remount/multi-lease-public-{suffix}.bin");
    let hash_path = format!("compact-remount/multi-lease-private-hash-{suffix}.sha256");
    let hash_tmp = format!("compact-remount/multi-lease-private-hash-{suffix}.tmp");

    let mut old_snapshot_content = String::new();
    let mut live_snapshot_content = String::new();
    for index in 0..4 {
        let content = versioned_payload("old-lease-prefix", index, 256 * 1024);
        if index == 3 {
            old_snapshot_content.clone_from(&content);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_path,
                "content": content,
                "overwrite": true,
            }),
        )?;
    }
    call_ok_as(
        &lease,
        &old_caller,
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({}),
    )?;

    for index in 4..12 {
        let content = versioned_payload("live-lease-suffix", index, 256 * 1024);
        if index == 11 {
            live_snapshot_content.clone_from(&content);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_path,
                "content": content,
                "overwrite": true,
            }),
        )?;
    }

    let enter_live = call_ok_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({}),
    )?;
    let workspace_root = as_str(&enter_live, "workspace_root")?.to_owned();
    let expected_hash = sha256_hex(&[live_snapshot_content.as_bytes()]);
    let command = format!(
        "bash -lc 'set -euo pipefail; printf MULTI_LEASE_READY; read -r _; actual=$(cat \"{workspace_root}/{public_path}\" | sha256sum); actual=${{actual%% *}}; test \"$actual\" = \"{expected_hash}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_tmp}\"; mv \"{workspace_root}/{hash_tmp}\" \"{workspace_root}/{hash_path}\"; printf MULTI_LEASE_DONE; sleep 30'"
    );
    let started = call_ok_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 60,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable command on the newer lease: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();

    let body = (|| -> Result<()> {
        wait_for_command_stdout_contains_as(
            &lease,
            &live_caller,
            &command_id,
            "MULTI_LEASE_READY",
        )?;
        let held = wait_for_active_leases(&lease, 2)?;
        ensure!(
            as_i64(&held, "manifest_depth")? >= 12,
            "test requires a deep active chain while two leases are open: {held}"
        );

        let old_read_before = call_ok_as(
            &lease,
            &old_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_path}),
        )?;
        ensure!(
            as_str(&old_read_before, "content")? == old_snapshot_content,
            "older open lease should keep its pre-suffix snapshot before remount: {old_read_before}"
        );

        let remount = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({
                "probe_path": &public_path,
                "probe_content": &live_snapshot_content,
            }),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "newer lease should live-remount and retarget despite an older open lease: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "active_leases_after")? == 2,
            "older open lease must remain active while the newer lease retargets: {remount}"
        );
        ensure!(
            as_i64(&remount, "after_layer_dirs")? < as_i64(&remount, "before_layer_dirs")?
                && as_i64(&remount, "after_storage_bytes")?
                    < as_i64(&remount, "before_storage_bytes")?,
            "remount should reclaim unpinned storage but remain bounded by the older lease: {remount}"
        );
        ensure!(
            as_i64(&remount, "after_layer_dirs")? > 3,
            "older open lease should keep some historical lowerdirs pinned after live remount: {remount}"
        );

        call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        wait_for_command_stdout_contains_as(&lease, &live_caller, &command_id, "MULTI_LEASE_DONE")?;

        let hash_read = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &hash_path}),
        )?;
        ensure!(
            as_str(&hash_read, "content")? == expected_hash,
            "resumed command on newer lease should read the compacted live snapshot: {hash_read}"
        );
        let old_read_after = call_ok_as(
            &lease,
            &old_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_path}),
        )?;
        ensure!(
            as_str(&old_read_after, "content")? == old_snapshot_content,
            "older open lease should still read its pinned snapshot after newer lease remount: {old_read_after}"
        );
        let live_read_after = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_path}),
        )?;
        ensure!(
            as_str(&live_read_after, "content")? == live_snapshot_content,
            "newer remounted lease should read the latest snapshot after retarget: {live_read_after}"
        );
        Ok(())
    })();

    let _ = call_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = call_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_ISOLATION_EXIT,
        json!({"grace_s": 0.1}),
    );
    let _ = call_as(
        &lease,
        &old_caller,
        catalog::SANDBOX_ISOLATION_EXIT,
        json!({"grace_s": 0.1}),
    );
    let released = wait_for_active_leases(&lease, 0);
    body?;
    released?;

    let public_read = call_ok_as(
        &lease,
        &public_caller,
        catalog::SANDBOX_FILE_READ,
        json!({"path": public_path}),
    )?;
    ensure!(
        as_str(&public_read, "content")? == live_snapshot_content,
        "public head should keep the latest content after both leases exit: {public_read}"
    );
    Ok(())
}

#[test]
fn compact_remount_live_remount_with_two_historical_leases_and_two_commands_preserves_all_snapshots(
) -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let old_one_caller = format!("remount-old-one-{suffix}");
    let old_two_caller = format!("remount-old-two-{suffix}");
    let live_caller = format!("remount-live-three-{suffix}");
    let public_caller = format!("remount-public-three-{suffix}");
    let public_a = format!("compact-remount/three-lease-a-{suffix}.bin");
    let public_b = format!("compact-remount/three-lease-b-{suffix}.bin");
    let hash_a = format!("compact-remount/three-lease-private-a-{suffix}.sha256");
    let hash_a_tmp = format!("compact-remount/three-lease-private-a-{suffix}.tmp");
    let hash_b = format!("compact-remount/three-lease-private-b-{suffix}.sha256");
    let hash_b_tmp = format!("compact-remount/three-lease-private-b-{suffix}.tmp");

    let mut old_one_a = String::new();
    let mut old_one_b = String::new();
    let mut old_two_a = String::new();
    let mut old_two_b = String::new();
    let mut live_a = String::new();
    let mut live_b = String::new();

    for revision in 0..4 {
        let content_a = versioned_payload("three-lease-old-one-a", revision, 128 * 1024);
        let content_b = versioned_payload("three-lease-old-one-b", revision, 128 * 1024);
        if revision == 3 {
            old_one_a.clone_from(&content_a);
            old_one_b.clone_from(&content_b);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_a,
                "content": content_a,
                "overwrite": true,
            }),
        )?;
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_b,
                "content": content_b,
                "overwrite": true,
            }),
        )?;
    }
    call_ok_as(
        &lease,
        &old_one_caller,
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({}),
    )?;

    for revision in 0..4 {
        let content_a = versioned_payload("three-lease-old-two-a", revision, 128 * 1024);
        let content_b = versioned_payload("three-lease-old-two-b", revision, 128 * 1024);
        if revision == 3 {
            old_two_a.clone_from(&content_a);
            old_two_b.clone_from(&content_b);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_a,
                "content": content_a,
                "overwrite": true,
            }),
        )?;
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_b,
                "content": content_b,
                "overwrite": true,
            }),
        )?;
    }
    call_ok_as(
        &lease,
        &old_two_caller,
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({}),
    )?;

    for revision in 0..4 {
        let content_a = versioned_payload("three-lease-live-a", revision, 128 * 1024);
        let content_b = versioned_payload("three-lease-live-b", revision, 128 * 1024);
        if revision == 3 {
            live_a.clone_from(&content_a);
            live_b.clone_from(&content_b);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_a,
                "content": content_a,
                "overwrite": true,
            }),
        )?;
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_b,
                "content": content_b,
                "overwrite": true,
            }),
        )?;
    }

    let enter_live = call_ok_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({}),
    )?;
    let workspace_root = as_str(&enter_live, "workspace_root")?.to_owned();
    let expected_ab = sha256_hex(&[live_a.as_bytes(), live_b.as_bytes()]);
    let expected_ba = sha256_hex(&[live_b.as_bytes(), live_a.as_bytes()]);
    let public_head_after_remount =
        versioned_payload("three-lease-public-after-remount", 0, 128 * 1024);

    let command_a = format!(
        "bash -lc 'set -euo pipefail; printf THREE_LEASE_A_READY; read -r _; actual=$(cat \"{workspace_root}/{public_a}\" \"{workspace_root}/{public_b}\" | sha256sum); actual=${{actual%% *}}; test \"$actual\" = \"{expected_ab}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_a_tmp}\"; mv \"{workspace_root}/{hash_a_tmp}\" \"{workspace_root}/{hash_a}\"; printf THREE_LEASE_A_DONE; sleep 30'"
    );
    let command_b = format!(
        "bash -lc 'set -euo pipefail; printf THREE_LEASE_B_READY; read -r _; python3 - <<\"PY\"\nimport hashlib, os\nh = hashlib.sha256()\nfor path in [\"{workspace_root}/{public_b}\", \"{workspace_root}/{public_a}\"]:\n    with open(path, \"rb\") as f:\n        while True:\n            chunk = f.read(32768)\n            if not chunk:\n                break\n            h.update(chunk)\nactual = h.hexdigest()\nif actual != \"{expected_ba}\":\n    raise SystemExit(\"hash mismatch \" + actual)\ntmp = \"{workspace_root}/{hash_b_tmp}\"\ndst = \"{workspace_root}/{hash_b}\"\nwith open(tmp, \"w\") as f:\n    f.write(actual)\nos.replace(tmp, dst)\nPY\nprintf THREE_LEASE_B_DONE; sleep 30'"
    );

    let started_a = call_ok_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command_a,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 90,
        }),
    )?;
    let started_b = call_ok_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command_b,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 90,
        }),
    )?;
    ensure!(
        as_str(&started_a, "status")? == "running" && as_str(&started_b, "status")? == "running",
        "test requires two live remountable commands on the newest lease: {started_a} {started_b}"
    );
    let command_a_id = as_str(&started_a, "command_id")?.to_owned();
    let command_b_id = as_str(&started_b, "command_id")?.to_owned();

    let body = (|| -> Result<()> {
        wait_for_command_stdout_contains_as(
            &lease,
            &live_caller,
            &command_a_id,
            "THREE_LEASE_A_READY",
        )?;
        wait_for_command_stdout_contains_as(
            &lease,
            &live_caller,
            &command_b_id,
            "THREE_LEASE_B_READY",
        )?;
        let held = wait_for_active_leases(&lease, 3)?;
        ensure!(
            as_i64(&held, "manifest_depth")? >= 24,
            "test requires three open leases over a deep active chain: {held}"
        );

        let old_one_before = call_ok_as(
            &lease,
            &old_one_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_a}),
        )?;
        ensure!(
            as_str(&old_one_before, "content")? == old_one_a,
            "first historical lease should read its pinned snapshot before remount: {old_one_before}"
        );
        let old_two_before = call_ok_as(
            &lease,
            &old_two_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_b}),
        )?;
        ensure!(
            as_str(&old_two_before, "content")? == old_two_b,
            "second historical lease should read its pinned snapshot before remount: {old_two_before}"
        );

        let remount = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({
                "probe_path": &public_b,
                "probe_content": &live_b,
            }),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "newest lease should live-remount despite two older open leases: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "active_leases_after")? == 3,
            "both historical leases must remain active after newest lease retarget: {remount}"
        );
        ensure!(
            as_i64(&remount, "remountable_commands")? == 2
                && as_i64(&remount, "process_count")? >= 2
                && as_i64(&remount, "quiesced_process_count")?
                    == as_i64(&remount, "process_count")?,
            "remount should quiesce both remountable command groups: {remount}"
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "safe commands should not pin the old mount while historical leases stay open: {remount}"
        );
        ensure!(
            as_i64(&remount, "after_layer_dirs")? < as_i64(&remount, "before_layer_dirs")?
                && as_i64(&remount, "after_layer_dirs")? > 3
                && as_i64(&remount, "after_storage_bytes")?
                    < as_i64(&remount, "before_storage_bytes")?,
            "remount should reclaim unpinned suffix while retaining historical layers: {remount}"
        );

        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_a,
                "content": &public_head_after_remount,
                "overwrite": true,
            }),
        )?;

        call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_a_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_b_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        wait_for_command_stdout_contains_as(
            &lease,
            &live_caller,
            &command_a_id,
            "THREE_LEASE_A_DONE",
        )?;
        wait_for_command_stdout_contains_as(
            &lease,
            &live_caller,
            &command_b_id,
            "THREE_LEASE_B_DONE",
        )?;

        let hash_a_read = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &hash_a}),
        )?;
        ensure!(
            as_str(&hash_a_read, "content")? == expected_ab,
            "command A should hash the newest pinned snapshot after remount: {hash_a_read}"
        );
        let hash_b_read = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &hash_b}),
        )?;
        ensure!(
            as_str(&hash_b_read, "content")? == expected_ba,
            "command B should hash the same remounted snapshot through a Python chunk reader: {hash_b_read}"
        );

        let old_one_after = call_ok_as(
            &lease,
            &old_one_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_b}),
        )?;
        ensure!(
            as_str(&old_one_after, "content")? == old_one_b,
            "first historical lease should still read its original snapshot after newest remount: {old_one_after}"
        );
        let old_two_after = call_ok_as(
            &lease,
            &old_two_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_a}),
        )?;
        ensure!(
            as_str(&old_two_after, "content")? == old_two_a,
            "second historical lease should still read its middle snapshot after newest remount: {old_two_after}"
        );
        let live_after = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_a}),
        )?;
        ensure!(
            as_str(&live_after, "content")? == live_a,
            "newest remounted lease should not observe public head movement after remount: {live_after}"
        );
        let public_after = call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_a}),
        )?;
        ensure!(
            as_str(&public_after, "content")? == public_head_after_remount,
            "public caller should see the post-remount head update: {public_after}"
        );
        Ok(())
    })();

    let _ = call_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_a_id}),
    );
    let _ = call_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_b_id}),
    );
    let _ = call_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_ISOLATION_EXIT,
        json!({"grace_s": 0.1}),
    );
    let _ = call_as(
        &lease,
        &old_two_caller,
        catalog::SANDBOX_ISOLATION_EXIT,
        json!({"grace_s": 0.1}),
    );
    let _ = call_as(
        &lease,
        &old_one_caller,
        catalog::SANDBOX_ISOLATION_EXIT,
        json!({"grace_s": 0.1}),
    );
    let released = wait_for_active_leases(&lease, 0);
    body?;
    released?;
    Ok(())
}

#[test]
fn compact_remount_live_remount_large_single_file_rewrite_keeps_snapshot() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_caller = format!("remount-large-public-{suffix}");
    let public_path = format!("compact-remount/large-single-{suffix}.bin");
    let hash_path = format!("compact-remount/large-single-hash-{suffix}.sha256");
    let hash_tmp = format!("compact-remount/large-single-hash-{suffix}.tmp");

    let payload_bytes = 1024 * 1024usize;
    let mut pinned_content = String::new();
    for revision in 0..9 {
        let content = versioned_payload("large-single-remount", revision, payload_bytes);
        if revision == 8 {
            pinned_content.clone_from(&content);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_path,
                "content": content,
                "overwrite": true,
            }),
        )?;
    }
    let pinned_hash = sha256_hex(&[pinned_content.as_bytes()]);
    let public_head_after_remount =
        versioned_payload("large-single-public-after-remount", 0, payload_bytes);

    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command = format!(
        "bash -lc 'set -euo pipefail; printf LARGE_SINGLE_READY; read -r _; actual=$(sha256sum \"{workspace_root}/{public_path}\"); actual=${{actual%% *}}; test \"$actual\" = \"{pinned_hash}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_tmp}\"; mv \"{workspace_root}/{hash_tmp}\" \"{workspace_root}/{hash_path}\"; printf LARGE_SINGLE_DONE; sleep 30'"
    );
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 120,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable large-file command: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();

    let body = (|| -> Result<()> {
        wait_for_command_stdout_contains(&lease, &command_id, "LARGE_SINGLE_READY")?;
        let remount = lease.call_ok(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({
                "probe_path": &public_path,
                "probe_content": &pinned_content,
            }),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "large-file remount should verify before lease retarget: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "before_layer_dirs")? >= 9
                && as_i64(&remount, "after_layer_dirs")? <= 3,
            "large-file remount should reclaim the retained rewrite chain: {remount}"
        );
        ensure!(
            as_i64(&remount, "before_storage_bytes")? >= (8 * payload_bytes) as i64
                && as_i64(&remount, "after_storage_bytes")?
                    < as_i64(&remount, "before_storage_bytes")?,
            "large-file remount should reduce retained bytes while preserving the lease: {remount}"
        );
        ensure!(
            as_i64(&remount, "process_count")? >= 1
                && as_i64(&remount, "quiesced_process_count")?
                    == as_i64(&remount, "process_count")?,
            "large-file remount should quiesce the running command: {remount}"
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "large-file command should not pin the old mount: {remount}"
        );

        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_path,
                "content": &public_head_after_remount,
                "overwrite": true,
            }),
        )?;
        lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        wait_for_command_stdout_contains(&lease, &command_id, "LARGE_SINGLE_DONE")?;

        let hash_read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &hash_path}))?;
        ensure!(
            as_str(&hash_read, "content")? == pinned_hash,
            "resumed large-file command should hash the pinned remounted file: {hash_read}"
        );
        let isolated_read =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &public_path}))?;
        ensure!(
            as_str(&isolated_read, "content")? == pinned_content,
            "large-file isolated lease should not observe public head movement: {isolated_read}"
        );
        let public_read = call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_path}),
        )?;
        ensure!(
            as_str(&public_read, "content")? == public_head_after_remount,
            "public caller should see the post-remount large-file head: {public_read}"
        );
        Ok(())
    })();

    let _ = lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_live_remount_after_historical_releases_reclaims_to_bounded_dirs() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_caller = format!("remount-release-public-{suffix}");
    let old_one_caller = format!("remount-release-old-one-{suffix}");
    let old_two_caller = format!("remount-release-old-two-{suffix}");
    let old_three_caller = format!("remount-release-old-three-{suffix}");
    let live_caller = format!("remount-release-live-{suffix}");
    let public_path = format!("compact-remount/release-pinned-{suffix}.bin");
    let hash_path = format!("compact-remount/release-private-hash-{suffix}.sha256");
    let hash_tmp = format!("compact-remount/release-private-hash-{suffix}.tmp");

    let mut old_one_content = String::new();
    let mut old_two_content = String::new();
    let mut old_three_content = String::new();
    let mut live_content = String::new();
    for revision in 0..4 {
        let content = versioned_payload("release-old-one", revision, 128 * 1024);
        if revision == 3 {
            old_one_content.clone_from(&content);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({"path": &public_path, "content": content, "overwrite": true}),
        )?;
    }
    call_ok_as(
        &lease,
        &old_one_caller,
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({}),
    )?;

    for revision in 0..4 {
        let content = versioned_payload("release-old-two", revision, 128 * 1024);
        if revision == 3 {
            old_two_content.clone_from(&content);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({"path": &public_path, "content": content, "overwrite": true}),
        )?;
    }
    call_ok_as(
        &lease,
        &old_two_caller,
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({}),
    )?;

    for revision in 0..4 {
        let content = versioned_payload("release-old-three", revision, 128 * 1024);
        if revision == 3 {
            old_three_content.clone_from(&content);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({"path": &public_path, "content": content, "overwrite": true}),
        )?;
    }
    call_ok_as(
        &lease,
        &old_three_caller,
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({}),
    )?;

    for revision in 0..4 {
        let content = versioned_payload("release-live", revision, 128 * 1024);
        if revision == 3 {
            live_content.clone_from(&content);
        }
        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({"path": &public_path, "content": content, "overwrite": true}),
        )?;
    }
    let enter_live = call_ok_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({}),
    )?;
    let workspace_root = as_str(&enter_live, "workspace_root")?.to_owned();
    let live_hash = sha256_hex(&[live_content.as_bytes()]);
    let public_head_after_release =
        versioned_payload("release-public-after-old-leases", 0, 128 * 1024);
    let command = format!(
        "bash -lc 'set -euo pipefail; printf RELEASE_RECLAIM_READY; read -r _; actual=$(sha256sum \"{workspace_root}/{public_path}\"); actual=${{actual%% *}}; test \"$actual\" = \"{live_hash}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_tmp}\"; mv \"{workspace_root}/{hash_tmp}\" \"{workspace_root}/{hash_path}\"; printf RELEASE_RECLAIM_DONE; sleep 30'"
    );
    let started = call_ok_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 120,
        }),
    )?;
    ensure!(
        as_str(&started, "status")? == "running",
        "test requires a live remountable command while historical leases release: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();

    let body = (|| -> Result<()> {
        wait_for_command_stdout_contains_as(
            &lease,
            &live_caller,
            &command_id,
            "RELEASE_RECLAIM_READY",
        )?;
        let held = wait_for_active_leases(&lease, 4)?;
        ensure!(
            as_i64(&held, "manifest_depth")? >= 16,
            "test requires a deep active chain with four open leases: {held}"
        );

        let first_remount = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({"probe_path": &public_path, "probe_content": &live_content}),
        )?;
        ensure!(
            first_remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && first_remount
                    .get("mount_verified")
                    .and_then(Value::as_bool)
                    == Some(true)
                && first_remount
                    .get("lease_retargeted")
                    .and_then(Value::as_bool)
                    == Some(true),
            "first remount should retarget the newest lease despite historical pins: {first_remount}"
        );
        assert_lowerdir_proof_fields(&first_remount)?;
        ensure!(
            as_i64(&first_remount, "active_leases_after")? == 4
                && as_i64(&first_remount, "after_layer_dirs")? > 3,
            "historical leases should still pin retained lowerdirs after first remount: {first_remount}"
        );

        for (caller, expected, label) in [
            (&old_one_caller, &old_one_content, "old-one"),
            (&old_two_caller, &old_two_content, "old-two"),
            (&old_three_caller, &old_three_content, "old-three"),
        ] {
            let read = call_ok_as(
                &lease,
                caller,
                catalog::SANDBOX_FILE_READ,
                json!({"path": &public_path}),
            )?;
            ensure!(
                as_str(&read, "content")? == expected,
                "{label} historical lease should preserve its snapshot before release: {read}"
            );
        }

        for caller in [&old_one_caller, &old_two_caller, &old_three_caller] {
            call_ok_as(
                &lease,
                caller,
                catalog::SANDBOX_ISOLATION_EXIT,
                json!({"grace_s": 0.1}),
            )?;
        }
        wait_for_active_leases(&lease, 1)?;

        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": &public_path,
                "content": &public_head_after_release,
                "overwrite": true,
            }),
        )?;
        let second_remount = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({"probe_path": &public_path, "probe_content": &live_content}),
        )?;
        ensure!(
            second_remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && second_remount
                    .get("mount_verified")
                    .and_then(Value::as_bool)
                    == Some(true)
                && second_remount
                    .get("lease_retargeted")
                    .and_then(Value::as_bool)
                    == Some(true),
            "second remount should retarget after historical leases release: {second_remount}"
        );
        assert_lowerdir_proof_fields(&second_remount)?;
        ensure!(
            as_i64(&second_remount, "active_leases_after")? == 1
                && as_i64(&second_remount, "after_layer_dirs")? <= 3
                && as_i64(&second_remount, "after_layer_dirs")?
                    < as_i64(&first_remount, "after_layer_dirs")?,
            "releasing historical leases should let live remount reclaim to bounded dirs: {second_remount}"
        );

        call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
        )?;
        wait_for_command_stdout_contains_as(
            &lease,
            &live_caller,
            &command_id,
            "RELEASE_RECLAIM_DONE",
        )?;
        let hash_read = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &hash_path}),
        )?;
        ensure!(
            as_str(&hash_read, "content")? == live_hash,
            "resumed command should still read the newest pinned snapshot: {hash_read}"
        );
        let live_read = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_path}),
        )?;
        ensure!(
            as_str(&live_read, "content")? == live_content,
            "live lease should stay pinned after old leases release and public head moves: {live_read}"
        );
        let public_read = call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &public_path}),
        )?;
        ensure!(
            as_str(&public_read, "content")? == public_head_after_release,
            "public caller should see the post-release head update: {public_read}"
        );
        Ok(())
    })();

    let _ = call_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    );
    let _ = call_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_ISOLATION_EXIT,
        json!({"grace_s": 0.1}),
    );
    for caller in [&old_three_caller, &old_two_caller, &old_one_caller] {
        let _ = call_as(
            &lease,
            caller,
            catalog::SANDBOX_ISOLATION_EXIT,
            json!({"grace_s": 0.1}),
        );
    }
    let released = wait_for_active_leases(&lease, 0);
    body?;
    released?;
    Ok(())
}

#[test]
fn compact_remount_live_remount_three_commands_over_wide_tree_keep_integrity() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_caller = format!("remount-three-command-public-{suffix}");
    let tree_root = format!("compact-remount/three-command-tree-{suffix}");
    let manifest_path = format!("{tree_root}/manifest.txt");
    let hash_a = format!("{tree_root}/private-a.sha256");
    let hash_a_tmp = format!("{tree_root}/private-a.tmp");
    let hash_b = format!("{tree_root}/private-b.sha256");
    let hash_b_tmp = format!("{tree_root}/private-b.tmp");
    let hash_c = format!("{tree_root}/private-c.sha256");
    let hash_c_tmp = format!("{tree_root}/private-c.tmp");
    let private_state = format!("{tree_root}/private-c-state.txt");

    let file_count = 12usize;
    let rewrite_count = 4usize;
    let payload_bytes = 24 * 1024usize;
    let mut paths = Vec::with_capacity(file_count);
    let mut manifest = String::new();
    let mut final_contents = vec![String::new(); file_count];
    for index in 0..file_count {
        let path = format!("{tree_root}/branch-{}/leaf-{index:02}.bin", index % 4);
        manifest.push_str(&path);
        manifest.push('\n');
        paths.push(path);
    }
    for revision in 0..rewrite_count {
        for (index, path) in paths.iter().enumerate() {
            let content =
                versioned_payload(&format!("three-command-r{revision}"), index, payload_bytes);
            if revision + 1 == rewrite_count {
                final_contents[index].clone_from(&content);
            }
            call_ok_as(
                &lease,
                &public_caller,
                catalog::SANDBOX_FILE_WRITE,
                json!({"path": path, "content": &content, "overwrite": true}),
            )?;
        }
    }
    call_ok_as(
        &lease,
        &public_caller,
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": &manifest_path, "content": &manifest, "overwrite": true}),
    )?;

    let expected_order = {
        let chunks: Vec<&[u8]> = final_contents
            .iter()
            .map(|content| content.as_bytes())
            .collect();
        sha256_hex(&chunks)
    };
    let expected_reverse = {
        let chunks: Vec<&[u8]> = final_contents
            .iter()
            .rev()
            .map(|content| content.as_bytes())
            .collect();
        sha256_hex(&chunks)
    };
    let private_content = format!("three-command-private-before-remount-{suffix}\n");
    let expected_even = {
        let mut chunks = Vec::with_capacity(1 + file_count / 2);
        chunks.push(private_content.as_bytes());
        for (index, content) in final_contents.iter().enumerate() {
            if index % 2 == 0 {
                chunks.push(content.as_bytes());
            }
        }
        sha256_hex(&chunks)
    };
    let probe_path = paths[file_count - 1].clone();
    let probe_content = final_contents[file_count - 1].clone();
    let moved_path = paths[0].clone();
    let moved_original = final_contents[0].clone();
    let moved_public = versioned_payload("three-command-public-after-remount", 0, payload_bytes);

    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let command_a = format!(
        "bash -lc 'set -euo pipefail; printf THREE_CMD_A_READY; read -r _; actual=$(while IFS= read -r p; do cat \"{workspace_root}/$p\"; done < \"{workspace_root}/{manifest_path}\" | sha256sum); actual=${{actual%% *}}; test \"$actual\" = \"{expected_order}\"; printf \"%s\" \"$actual\" > \"{workspace_root}/{hash_a_tmp}\"; mv \"{workspace_root}/{hash_a_tmp}\" \"{workspace_root}/{hash_a}\"; printf THREE_CMD_A_DONE; sleep 30'"
    );
    let command_b = format!(
        "bash -lc 'set -euo pipefail; printf THREE_CMD_B_READY; read -r _; python3 - <<\"PY\"\nimport hashlib, os\nroot = \"{workspace_root}\"\nmanifest = \"{workspace_root}/{manifest_path}\"\nwith open(manifest) as f:\n    paths = [line.strip() for line in f if line.strip()]\nh = hashlib.sha256()\nfor rel in reversed(paths):\n    with open(os.path.join(root, rel), \"rb\") as f:\n        h.update(f.read())\nactual = h.hexdigest()\nif actual != \"{expected_reverse}\":\n    raise SystemExit(\"reverse hash mismatch \" + actual)\ntmp = \"{workspace_root}/{hash_b_tmp}\"\ndst = \"{workspace_root}/{hash_b}\"\nwith open(tmp, \"w\") as f:\n    f.write(actual)\nos.replace(tmp, dst)\nPY\nprintf THREE_CMD_B_DONE; sleep 30'"
    );
    let command_c = format!(
        "bash -lc 'set -euo pipefail; printf \"%s\" \"{private_content}\" > \"{workspace_root}/{private_state}\"; printf THREE_CMD_C_READY; read -r _; python3 - <<\"PY\"\nimport hashlib, os\nroot = \"{workspace_root}\"\nmanifest = \"{workspace_root}/{manifest_path}\"\nprivate_state = \"{workspace_root}/{private_state}\"\nwith open(manifest) as f:\n    paths = [line.strip() for line in f if line.strip()]\nh = hashlib.sha256()\nwith open(private_state, \"rb\") as f:\n    h.update(f.read())\nfor index, rel in enumerate(paths):\n    if index % 2 == 0:\n        with open(os.path.join(root, rel), \"rb\") as f:\n            h.update(f.read())\nactual = h.hexdigest()\nif actual != \"{expected_even}\":\n    raise SystemExit(\"even hash mismatch \" + actual)\ntmp = \"{workspace_root}/{hash_c_tmp}\"\ndst = \"{workspace_root}/{hash_c}\"\nwith open(tmp, \"w\") as f:\n    f.write(actual)\nos.replace(tmp, dst)\nPY\nprintf THREE_CMD_C_DONE; sleep 30'"
    );

    let started_a = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command_a,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 120,
        }),
    )?;
    let started_b = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command_b,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 120,
        }),
    )?;
    let started_c = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": command_c,
            "cwd": "/tmp",
            "remountable": true,
            "yield_time_ms": 500,
            "timeout_seconds": 120,
        }),
    )?;
    ensure!(
        as_str(&started_a, "status")? == "running"
            && as_str(&started_b, "status")? == "running"
            && as_str(&started_c, "status")? == "running",
        "test requires three live remountable commands: {started_a} {started_b} {started_c}"
    );
    let command_a_id = as_str(&started_a, "command_id")?.to_owned();
    let command_b_id = as_str(&started_b, "command_id")?.to_owned();
    let command_c_id = as_str(&started_c, "command_id")?.to_owned();

    let body = (|| -> Result<()> {
        wait_for_command_stdout_contains(&lease, &command_a_id, "THREE_CMD_A_READY")?;
        wait_for_command_stdout_contains(&lease, &command_b_id, "THREE_CMD_B_READY")?;
        wait_for_command_stdout_contains(&lease, &command_c_id, "THREE_CMD_C_READY")?;
        let private_read =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &private_state}))?;
        ensure!(
            as_str(&private_read, "content")? == private_content,
            "third command should create private state before remount: {private_read}"
        );

        let remount = lease.call_ok(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({"probe_path": &probe_path, "probe_content": &probe_content}),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "three-command wide-tree remount should verify before retarget: {remount}"
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "remountable_commands")? == 3
                && as_i64(&remount, "process_count")? >= 3
                && as_i64(&remount, "quiesced_process_count")?
                    == as_i64(&remount, "process_count")?,
            "remount should quiesce all three command groups: {remount}"
        );
        ensure!(
            as_i64(&remount, "before_layer_dirs")? >= (file_count * rewrite_count) as i64
                && as_i64(&remount, "after_layer_dirs")? <= 3,
            "wide-tree remount should compact many retained layers to bounded dirs: {remount}"
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "wide-tree commands should not pin the old mount: {remount}"
        );

        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({"path": &moved_path, "content": &moved_public, "overwrite": true}),
        )?;
        for command_id in [&command_a_id, &command_b_id, &command_c_id] {
            lease.call_ok(
                catalog::SANDBOX_COMMAND_WRITE_STDIN,
                json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
            )?;
        }
        wait_for_command_stdout_contains(&lease, &command_a_id, "THREE_CMD_A_DONE")?;
        wait_for_command_stdout_contains(&lease, &command_b_id, "THREE_CMD_B_DONE")?;
        wait_for_command_stdout_contains(&lease, &command_c_id, "THREE_CMD_C_DONE")?;

        for (path, expected, label) in [
            (&hash_a, &expected_order, "ordered"),
            (&hash_b, &expected_reverse, "reverse"),
            (&hash_c, &expected_even, "private-even"),
        ] {
            let read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": path}))?;
            ensure!(
                as_str(&read, "content")? == expected,
                "{label} command should preserve its integrity hash after remount: {read}"
            );
        }
        let isolated_read =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &moved_path}))?;
        ensure!(
            as_str(&isolated_read, "content")? == moved_original,
            "wide-tree isolated lease should not observe post-remount public update: {isolated_read}"
        );
        let public_read = call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &moved_path}),
        )?;
        ensure!(
            as_str(&public_read, "content")? == moved_public,
            "public caller should see the wide-tree post-remount update: {public_read}"
        );
        Ok(())
    })();

    for command_id in [&command_a_id, &command_b_id, &command_c_id] {
        let _ = lease.call(
            catalog::SANDBOX_COMMAND_CANCEL,
            json!({"command_id": command_id}),
        );
    }
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

#[test]
fn compact_remount_live_remount_matrix_single_large_hot_file() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-large-hot-file",
        file_count: 1,
        rewrite_count: 12,
        payload_bytes: 512 * 1024,
        command_count: 1,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_matrix_deep_tree_two_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-deep-tree-two",
        file_count: 18,
        rewrite_count: 3,
        payload_bytes: 16 * 1024,
        command_count: 2,
        branch_count: 6,
        path_stride: 5,
    })
}

#[test]
fn compact_remount_live_remount_matrix_many_tiny_files_three_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-many-tiny-three",
        file_count: 36,
        rewrite_count: 2,
        payload_bytes: 4 * 1024,
        command_count: 3,
        branch_count: 9,
        path_stride: 7,
    })
}

#[test]
fn compact_remount_live_remount_matrix_medium_large_four_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-medium-large-four",
        file_count: 10,
        rewrite_count: 5,
        payload_bytes: 64 * 1024,
        command_count: 4,
        branch_count: 5,
        path_stride: 3,
    })
}

#[test]
fn compact_remount_live_remount_matrix_wide_sparse_two_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-wide-sparse-two",
        file_count: 48,
        rewrite_count: 1,
        payload_bytes: 8 * 1024,
        command_count: 2,
        branch_count: 12,
        path_stride: 11,
    })
}

#[test]
fn compact_remount_live_remount_matrix_nested_rewrite_four_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-nested-rewrite-four",
        file_count: 16,
        rewrite_count: 4,
        payload_bytes: 32 * 1024,
        command_count: 4,
        branch_count: 8,
        path_stride: 9,
    })
}

#[test]
fn compact_remount_live_remount_matrix_easy_hot_pair_one_command() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-easy-hot-pair-one",
        file_count: 2,
        rewrite_count: 6,
        payload_bytes: 8 * 1024,
        command_count: 1,
        branch_count: 2,
        path_stride: 3,
    })
}

#[test]
fn compact_remount_live_remount_matrix_easy_four_files_two_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-easy-four-two",
        file_count: 4,
        rewrite_count: 4,
        payload_bytes: 12 * 1024,
        command_count: 2,
        branch_count: 2,
        path_stride: 5,
    })
}

#[test]
fn compact_remount_live_remount_matrix_easy_sparse_tiny_one_command() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-easy-sparse-tiny-one",
        file_count: 24,
        rewrite_count: 1,
        payload_bytes: 1024,
        command_count: 1,
        branch_count: 6,
        path_stride: 7,
    })
}

#[test]
fn compact_remount_live_remount_matrix_easy_balanced_two_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-easy-balanced-two",
        file_count: 12,
        rewrite_count: 2,
        payload_bytes: 4 * 1024,
        command_count: 2,
        branch_count: 4,
        path_stride: 11,
    })
}

#[test]
fn compact_remount_live_remount_matrix_medium_sixty_four_files_four_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-medium-sixty-four-four",
        file_count: 64,
        rewrite_count: 2,
        payload_bytes: 8 * 1024,
        command_count: 4,
        branch_count: 16,
        path_stride: 17,
    })
}

#[test]
fn compact_remount_live_remount_matrix_medium_hot_quad_three_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-medium-hot-quad-three",
        file_count: 4,
        rewrite_count: 8,
        payload_bytes: 32 * 1024,
        command_count: 3,
        branch_count: 4,
        path_stride: 5,
    })
}

#[test]
fn compact_remount_live_remount_matrix_medium_mixed_twenty_four_five_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-medium-mixed-twenty-four-five",
        file_count: 24,
        rewrite_count: 3,
        payload_bytes: 16 * 1024,
        command_count: 5,
        branch_count: 8,
        path_stride: 19,
    })
}

#[test]
fn compact_remount_live_remount_matrix_medium_single_large_one_command() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-medium-single-large-one",
        file_count: 1,
        rewrite_count: 5,
        payload_bytes: 2 * 1024 * 1024,
        command_count: 1,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_matrix_hard_many_commands_rewrite_tree() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-hard-many-commands-tree",
        file_count: 32,
        rewrite_count: 4,
        payload_bytes: 32 * 1024,
        command_count: 8,
        branch_count: 16,
        path_stride: 23,
    })
}

#[test]
fn compact_remount_live_remount_matrix_hard_large_four_files_four_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-hard-large-four-four",
        file_count: 4,
        rewrite_count: 6,
        payload_bytes: 512 * 1024,
        command_count: 4,
        branch_count: 4,
        path_stride: 7,
    })
}

#[test]
fn compact_remount_live_remount_matrix_hard_wide_ninety_six_six_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-hard-wide-ninety-six-six",
        file_count: 96,
        rewrite_count: 2,
        payload_bytes: 8 * 1024,
        command_count: 6,
        branch_count: 24,
        path_stride: 29,
    })
}

#[test]
fn compact_remount_live_remount_matrix_hard_deep_twelve_six_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "matrix-hard-deep-twelve-six",
        file_count: 12,
        rewrite_count: 8,
        payload_bytes: 64 * 1024,
        command_count: 6,
        branch_count: 6,
        path_stride: 31,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_easy_micro_wide_manifest_one_command() -> Result<()>
{
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-easy-micro-wide-one",
        file_count: 40,
        rewrite_count: 1,
        payload_bytes: 512,
        command_count: 1,
        branch_count: 10,
        path_stride: 37,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_easy_hot_three_files_two_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-easy-hot-three-two",
        file_count: 3,
        rewrite_count: 5,
        payload_bytes: 4 * 1024,
        command_count: 2,
        branch_count: 3,
        path_stride: 5,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_easy_nested_twelve_one_command() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-easy-nested-twelve-one",
        file_count: 12,
        rewrite_count: 2,
        payload_bytes: 2 * 1024,
        command_count: 1,
        branch_count: 6,
        path_stride: 11,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_easy_command_pair_balanced() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-easy-command-pair-balanced",
        file_count: 8,
        rewrite_count: 3,
        payload_bytes: 4 * 1024,
        command_count: 2,
        branch_count: 4,
        path_stride: 13,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_medium_tiny_128_files_four_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-medium-tiny-128-four",
        file_count: 128,
        rewrite_count: 1,
        payload_bytes: 2 * 1024,
        command_count: 4,
        branch_count: 32,
        path_stride: 41,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_medium_six_files_512k_three_commands() -> Result<()>
{
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-medium-six-512k-three",
        file_count: 6,
        rewrite_count: 3,
        payload_bytes: 512 * 1024,
        command_count: 3,
        branch_count: 6,
        path_stride: 7,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_medium_32_files_32k_five_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-medium-32-32k-five",
        file_count: 32,
        rewrite_count: 3,
        payload_bytes: 32 * 1024,
        command_count: 5,
        branch_count: 16,
        path_stride: 43,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_medium_hot_one_16_rewrites_two_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-medium-hot-one-16-two",
        file_count: 1,
        rewrite_count: 16,
        payload_bytes: 128 * 1024,
        command_count: 2,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_hard_single_8mib_four_rewrites() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-hard-single-8mib-four",
        file_count: 1,
        rewrite_count: 4,
        payload_bytes: 8 * 1024 * 1024,
        command_count: 1,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_hard_64_files_four_rewrites_eight_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-hard-64-four-eight",
        file_count: 64,
        rewrite_count: 4,
        payload_bytes: 32 * 1024,
        command_count: 8,
        branch_count: 32,
        path_stride: 47,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_hard_192_sparse_two_rewrites_six_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-hard-192-sparse-six",
        file_count: 192,
        rewrite_count: 2,
        payload_bytes: 4 * 1024,
        command_count: 6,
        branch_count: 48,
        path_stride: 53,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_hard_hot_quad_1mib_six_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal2-hard-hot-quad-1mib-six",
        file_count: 4,
        rewrite_count: 6,
        payload_bytes: 1024 * 1024,
        command_count: 6,
        branch_count: 4,
        path_stride: 17,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_pinned_easy_two_leases_four_files() -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal2-pinned-easy-two-four",
        historical_lease_count: 2,
        file_count: 4,
        rewrites_per_generation: 2,
        payload_bytes: 8 * 1024,
        command_count: 2,
        branch_count: 4,
        path_stride: 7,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_pinned_medium_three_leases_twelve_files(
) -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal2-pinned-medium-three-twelve",
        historical_lease_count: 3,
        file_count: 12,
        rewrites_per_generation: 2,
        payload_bytes: 32 * 1024,
        command_count: 4,
        branch_count: 6,
        path_stride: 19,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_pinned_hard_four_leases_hot_512k() -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal2-pinned-hard-four-hot-512k",
        historical_lease_count: 4,
        file_count: 1,
        rewrites_per_generation: 3,
        payload_bytes: 512 * 1024,
        command_count: 2,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal2_pinned_hard_four_leases_twenty_four_files(
) -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal2-pinned-hard-four-twenty-four",
        historical_lease_count: 4,
        file_count: 24,
        rewrites_per_generation: 1,
        payload_bytes: 64 * 1024,
        command_count: 5,
        branch_count: 12,
        path_stride: 23,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_easy_two_files_long_rewrite_one_command(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-easy-two-long-one",
        file_count: 2,
        rewrite_count: 10,
        payload_bytes: 2 * 1024,
        command_count: 1,
        branch_count: 2,
        path_stride: 5,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_easy_fanout_sixty_four_one_command() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-easy-fanout-64-one",
        file_count: 64,
        rewrite_count: 1,
        payload_bytes: 768,
        command_count: 1,
        branch_count: 16,
        path_stride: 59,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_easy_three_commands_small_tree() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-easy-three-commands-small",
        file_count: 9,
        rewrite_count: 2,
        payload_bytes: 2 * 1024,
        command_count: 3,
        branch_count: 3,
        path_stride: 7,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_easy_single_hot_thirty_two_k() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-easy-single-hot-32k",
        file_count: 1,
        rewrite_count: 8,
        payload_bytes: 32 * 1024,
        command_count: 1,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_easy_twenty_files_four_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-easy-twenty-four-commands",
        file_count: 20,
        rewrite_count: 1,
        payload_bytes: 4 * 1024,
        command_count: 4,
        branch_count: 10,
        path_stride: 31,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_easy_five_files_three_rewrites_two_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-easy-five-three-two",
        file_count: 5,
        rewrite_count: 3,
        payload_bytes: 8 * 1024,
        command_count: 2,
        branch_count: 5,
        path_stride: 13,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_easy_96_sparse_two_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-easy-96-sparse-two",
        file_count: 96,
        rewrite_count: 1,
        payload_bytes: 1024,
        command_count: 2,
        branch_count: 24,
        path_stride: 61,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_pinned_easy_one_older_reader() -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal3-pinned-easy-one-reader",
        historical_lease_count: 1,
        file_count: 6,
        rewrites_per_generation: 2,
        payload_bytes: 4 * 1024,
        command_count: 2,
        branch_count: 3,
        path_stride: 11,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_medium_hot_pair_512k_three_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-medium-hot-pair-512k",
        file_count: 2,
        rewrite_count: 6,
        payload_bytes: 512 * 1024,
        command_count: 3,
        branch_count: 2,
        path_stride: 17,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_medium_256_tiny_files_five_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-medium-256-tiny-five",
        file_count: 256,
        rewrite_count: 1,
        payload_bytes: 1024,
        command_count: 5,
        branch_count: 64,
        path_stride: 67,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_medium_twelve_files_four_rewrites_four_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-medium-twelve-four-four",
        file_count: 12,
        rewrite_count: 4,
        payload_bytes: 64 * 1024,
        command_count: 4,
        branch_count: 6,
        path_stride: 23,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_medium_single_file_twenty_four_rewrites(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-medium-single-24",
        file_count: 1,
        rewrite_count: 24,
        payload_bytes: 64 * 1024,
        command_count: 3,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_medium_forty_eight_files_three_rewrites(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-medium-48-three",
        file_count: 48,
        rewrite_count: 3,
        payload_bytes: 8 * 1024,
        command_count: 6,
        branch_count: 24,
        path_stride: 71,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_pinned_medium_three_readers_hot_pair() -> Result<()>
{
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal3-pinned-medium-hot-pair",
        historical_lease_count: 3,
        file_count: 2,
        rewrites_per_generation: 3,
        payload_bytes: 128 * 1024,
        command_count: 3,
        branch_count: 2,
        path_stride: 7,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_hard_single_8mib_five_rewrites() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-hard-single-8mib-five",
        file_count: 1,
        rewrite_count: 5,
        payload_bytes: 8 * 1024 * 1024,
        command_count: 1,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_hard_eight_files_1mib_four_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-hard-eight-1mib-four",
        file_count: 8,
        rewrite_count: 4,
        payload_bytes: 1024 * 1024,
        command_count: 4,
        branch_count: 8,
        path_stride: 29,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_hard_320_sparse_files_eight_commands() -> Result<()>
{
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-hard-320-sparse-eight",
        file_count: 320,
        rewrite_count: 1,
        payload_bytes: 2 * 1024,
        command_count: 8,
        branch_count: 80,
        path_stride: 73,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_hard_32_files_five_rewrites_eight_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal3-hard-32-five-eight",
        file_count: 32,
        rewrite_count: 5,
        payload_bytes: 128 * 1024,
        command_count: 8,
        branch_count: 16,
        path_stride: 79,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_pinned_hard_four_readers_eight_files_512k(
) -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal3-pinned-hard-eight-512k",
        historical_lease_count: 4,
        file_count: 8,
        rewrites_per_generation: 2,
        payload_bytes: 512 * 1024,
        command_count: 4,
        branch_count: 8,
        path_stride: 31,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal3_pinned_hard_four_readers_64_sparse_files(
) -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal3-pinned-hard-64-sparse",
        historical_lease_count: 4,
        file_count: 64,
        rewrites_per_generation: 1,
        payload_bytes: 16 * 1024,
        command_count: 6,
        branch_count: 32,
        path_stride: 37,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_easy_single_16k_three_rewrites() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-easy-single-16k-three",
        file_count: 1,
        rewrite_count: 3,
        payload_bytes: 16 * 1024,
        command_count: 1,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_easy_six_files_two_rewrites_one_command(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-easy-six-two-one",
        file_count: 6,
        rewrite_count: 2,
        payload_bytes: 8 * 1024,
        command_count: 1,
        branch_count: 3,
        path_stride: 5,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_easy_eighteen_sparse_two_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-easy-eighteen-sparse-two",
        file_count: 18,
        rewrite_count: 1,
        payload_bytes: 2 * 1024,
        command_count: 2,
        branch_count: 6,
        path_stride: 41,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_easy_ten_files_three_rewrites_two_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-easy-ten-three-two",
        file_count: 10,
        rewrite_count: 3,
        payload_bytes: 4 * 1024,
        command_count: 2,
        branch_count: 5,
        path_stride: 17,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_easy_three_hot_files_one_command() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-easy-three-hot-one",
        file_count: 3,
        rewrite_count: 4,
        payload_bytes: 16 * 1024,
        command_count: 1,
        branch_count: 3,
        path_stride: 7,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_easy_twenty_four_tiny_three_commands() -> Result<()>
{
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-easy-twenty-four-tiny-three",
        file_count: 24,
        rewrite_count: 1,
        payload_bytes: 1024,
        command_count: 3,
        branch_count: 8,
        path_stride: 43,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_pinned_easy_one_reader_three_files() -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal4-pinned-easy-one-three",
        historical_lease_count: 1,
        file_count: 3,
        rewrites_per_generation: 1,
        payload_bytes: 8 * 1024,
        command_count: 1,
        branch_count: 3,
        path_stride: 11,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_pinned_easy_two_readers_two_files() -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal4-pinned-easy-two-two",
        historical_lease_count: 2,
        file_count: 2,
        rewrites_per_generation: 1,
        payload_bytes: 4 * 1024,
        command_count: 1,
        branch_count: 2,
        path_stride: 5,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_easy_nested_thirty_two_one_command() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-easy-nested-thirty-two-one",
        file_count: 32,
        rewrite_count: 1,
        payload_bytes: 2 * 1024,
        command_count: 1,
        branch_count: 16,
        path_stride: 47,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_easy_single_hot_eight_rewrites_two_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-easy-single-hot-eight-two",
        file_count: 1,
        rewrite_count: 8,
        payload_bytes: 2 * 1024,
        command_count: 2,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_easy_twelve_sparse_two_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-easy-twelve-sparse-two",
        file_count: 12,
        rewrite_count: 1,
        payload_bytes: 4 * 1024,
        command_count: 2,
        branch_count: 6,
        path_stride: 53,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_easy_four_files_three_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-easy-four-three",
        file_count: 4,
        rewrite_count: 2,
        payload_bytes: 8 * 1024,
        command_count: 3,
        branch_count: 4,
        path_stride: 13,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_medium_sixty_four_files_four_commands() -> Result<()>
{
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-medium-sixty-four-four",
        file_count: 64,
        rewrite_count: 2,
        payload_bytes: 4 * 1024,
        command_count: 4,
        branch_count: 16,
        path_stride: 59,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_medium_sixteen_files_four_rewrites_four_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-medium-sixteen-four-four",
        file_count: 16,
        rewrite_count: 4,
        payload_bytes: 32 * 1024,
        command_count: 4,
        branch_count: 8,
        path_stride: 19,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_medium_hot_pair_128k_three_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-medium-hot-pair-128k-three",
        file_count: 2,
        rewrite_count: 10,
        payload_bytes: 128 * 1024,
        command_count: 3,
        branch_count: 2,
        path_stride: 7,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_medium_single_file_twenty_rewrites_two_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-medium-single-twenty-two",
        file_count: 1,
        rewrite_count: 20,
        payload_bytes: 64 * 1024,
        command_count: 2,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_medium_forty_eight_files_five_commands() -> Result<()>
{
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-medium-forty-eight-five",
        file_count: 48,
        rewrite_count: 3,
        payload_bytes: 8 * 1024,
        command_count: 5,
        branch_count: 16,
        path_stride: 61,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_medium_eight_files_256k_four_commands() -> Result<()>
{
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-medium-eight-256k-four",
        file_count: 8,
        rewrite_count: 4,
        payload_bytes: 256 * 1024,
        command_count: 4,
        branch_count: 8,
        path_stride: 23,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_medium_128_sparse_five_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-medium-128-sparse-five",
        file_count: 128,
        rewrite_count: 2,
        payload_bytes: 2 * 1024,
        command_count: 5,
        branch_count: 32,
        path_stride: 67,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_pinned_medium_three_readers_six_files() -> Result<()>
{
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal4-pinned-medium-three-six",
        historical_lease_count: 3,
        file_count: 6,
        rewrites_per_generation: 2,
        payload_bytes: 32 * 1024,
        command_count: 2,
        branch_count: 6,
        path_stride: 17,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_pinned_medium_two_readers_twelve_files() -> Result<()>
{
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal4-pinned-medium-two-twelve",
        historical_lease_count: 2,
        file_count: 12,
        rewrites_per_generation: 3,
        payload_bytes: 16 * 1024,
        command_count: 3,
        branch_count: 6,
        path_stride: 29,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_medium_thirty_two_files_five_rewrites_six_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-medium-thirty-two-five-six",
        file_count: 32,
        rewrite_count: 5,
        payload_bytes: 16 * 1024,
        command_count: 6,
        branch_count: 16,
        path_stride: 71,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_hard_five_files_1mib_five_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-hard-five-1mib-five",
        file_count: 5,
        rewrite_count: 3,
        payload_bytes: 1024 * 1024,
        command_count: 5,
        branch_count: 5,
        path_stride: 31,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_hard_single_4mib_eight_rewrites_two_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-hard-single-4mib-eight-two",
        file_count: 1,
        rewrite_count: 8,
        payload_bytes: 4 * 1024 * 1024,
        command_count: 2,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_hard_256_sparse_eight_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-hard-256-sparse-eight",
        file_count: 256,
        rewrite_count: 2,
        payload_bytes: 2 * 1024,
        command_count: 8,
        branch_count: 64,
        path_stride: 73,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_hard_96_files_four_rewrites_eight_commands(
) -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-hard-96-four-eight",
        file_count: 96,
        rewrite_count: 4,
        payload_bytes: 8 * 1024,
        command_count: 8,
        branch_count: 32,
        path_stride: 79,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_pinned_hard_four_readers_sixteen_files() -> Result<()>
{
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal4-pinned-hard-four-sixteen",
        historical_lease_count: 4,
        file_count: 16,
        rewrites_per_generation: 3,
        payload_bytes: 64 * 1024,
        command_count: 6,
        branch_count: 16,
        path_stride: 37,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_pinned_hard_four_readers_hot_512k() -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "coverage-goal4-pinned-hard-four-hot-512k",
        historical_lease_count: 4,
        file_count: 1,
        rewrites_per_generation: 2,
        payload_bytes: 512 * 1024,
        command_count: 4,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_coverage_goal4_hard_64_files_64k_eight_commands() -> Result<()> {
    compact_remount_live_remount_matrix_case(RemountMatrixCase {
        name: "coverage-goal4-hard-64-64k-eight",
        file_count: 64,
        rewrite_count: 3,
        payload_bytes: 64 * 1024,
        command_count: 8,
        branch_count: 32,
        path_stride: 83,
    })
}

#[test]
fn compact_remount_live_remount_pinned_history_matrix_four_leases_large_hot_file() -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "pinned-history-four-large-hot",
        historical_lease_count: 4,
        file_count: 1,
        rewrites_per_generation: 4,
        payload_bytes: 256 * 1024,
        command_count: 1,
        branch_count: 1,
        path_stride: 1,
    })
}

#[test]
fn compact_remount_live_remount_pinned_history_matrix_three_leases_many_files() -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "pinned-history-three-many-files",
        historical_lease_count: 3,
        file_count: 20,
        rewrites_per_generation: 2,
        payload_bytes: 16 * 1024,
        command_count: 4,
        branch_count: 10,
        path_stride: 13,
    })
}

#[test]
fn compact_remount_live_remount_pinned_history_matrix_four_leases_sparse_tree() -> Result<()> {
    compact_remount_live_remount_pinned_history_matrix_case(RemountPinnedHistoryCase {
        name: "pinned-history-four-sparse-tree",
        historical_lease_count: 4,
        file_count: 12,
        rewrites_per_generation: 1,
        payload_bytes: 8 * 1024,
        command_count: 3,
        branch_count: 6,
        path_stride: 17,
    })
}

#[test]
fn compact_remount_reports_not_open_for_ephemeral_caller() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);

    let response = lease.call(catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT, json!({}))?;
    let error = response
        .get("error")
        .or_else(|| response.get("fault"))
        .context("expected rejected operation error")?;
    ensure!(
        as_str(error, "kind")? == "not_open",
        "host/public callers have no mounted lease to remount: {response}"
    );
    Ok(())
}

struct RemountMatrixCase {
    name: &'static str,
    file_count: usize,
    rewrite_count: usize,
    payload_bytes: usize,
    command_count: usize,
    branch_count: usize,
    path_stride: usize,
}

struct RemountPinnedHistoryCase {
    name: &'static str,
    historical_lease_count: usize,
    file_count: usize,
    rewrites_per_generation: usize,
    payload_bytes: usize,
    command_count: usize,
    branch_count: usize,
    path_stride: usize,
}

fn compact_remount_live_remount_matrix_case(case: RemountMatrixCase) -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_caller = format!("remount-{}-public-{suffix}", case.name);
    let tree_root = format!("compact-remount/{}-{suffix}", case.name);
    let manifest_path = format!("{tree_root}/manifest.txt");

    let mut paths = Vec::with_capacity(case.file_count);
    let mut manifest = String::new();
    let mut final_contents = vec![String::new(); case.file_count];
    for index in 0..case.file_count {
        let branch = index % case.branch_count.max(1);
        let shard = (index * case.path_stride) % 13;
        let path = format!("{tree_root}/branch-{branch}/shard-{shard}/leaf-{index:03}.bin");
        manifest.push_str(&path);
        manifest.push('\n');
        paths.push(path);
    }

    for revision in 0..case.rewrite_count {
        for (index, path) in paths.iter().enumerate() {
            let content = versioned_payload(
                &format!("{}-revision-{revision}", case.name),
                index,
                case.payload_bytes,
            );
            if revision + 1 == case.rewrite_count {
                final_contents[index].clone_from(&content);
            }
            call_ok_as(
                &lease,
                &public_caller,
                catalog::SANDBOX_FILE_WRITE,
                json!({"path": path, "content": &content, "overwrite": true}),
            )?;
        }
    }
    call_ok_as(
        &lease,
        &public_caller,
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": &manifest_path, "content": &manifest, "overwrite": true}),
    )?;

    let enter = lease.call_ok(catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
    let workspace_root = as_str(&enter, "workspace_root")?.to_owned();
    let moved_path = paths[0].clone();
    let moved_original = final_contents[0].clone();
    let moved_public = versioned_payload(
        &format!("{}-public-after-remount", case.name),
        0,
        case.payload_bytes,
    );
    let probe_path = paths[case.file_count - 1].clone();
    let probe_content = final_contents[case.file_count - 1].clone();

    let mut command_ids = Vec::with_capacity(case.command_count);
    let mut expected_hashes = Vec::with_capacity(case.command_count);
    let mut hash_paths = Vec::with_capacity(case.command_count);
    for command_index in 0..case.command_count {
        let private_state = format!("{tree_root}/private-matrix-{command_index}-{suffix}.state");
        let hash_path = format!("{tree_root}/private-matrix-{command_index}-{suffix}.sha256");
        let hash_tmp = format!("{tree_root}/private-matrix-{command_index}-{suffix}.tmp");
        let private_content = format!("{}-private-command-{command_index}-{suffix}\n", case.name);
        let mut chunks = Vec::new();
        chunks.push(private_content.as_bytes());
        for (index, content) in final_contents.iter().enumerate() {
            if index % case.command_count == command_index {
                chunks.push(content.as_bytes());
            }
        }
        let expected_hash = sha256_hex(&chunks);
        let ready_marker = format!("MATRIX_{command_index}_READY");
        let done_marker = format!("MATRIX_{command_index}_DONE");
        let command = format!(
            "bash -lc 'set -euo pipefail; printf \"%s\" \"{private_content}\" > \"{workspace_root}/{private_state}\"; printf {ready_marker}; read -r _; python3 - <<\"PY\"\nimport hashlib, os\nroot = \"{workspace_root}\"\nmanifest = \"{workspace_root}/{manifest_path}\"\nprivate_state = \"{workspace_root}/{private_state}\"\nexpected = \"{expected_hash}\"\ncommand_index = {command_index}\ncommand_count = {command_count}\nwith open(manifest) as f:\n    paths = [line.strip() for line in f if line.strip()]\nh = hashlib.sha256()\nwith open(private_state, \"rb\") as f:\n    h.update(f.read())\nfor index, rel in enumerate(paths):\n    if index % command_count == command_index:\n        with open(os.path.join(root, rel), \"rb\") as f:\n            while True:\n                chunk = f.read(32768)\n                if not chunk:\n                    break\n                h.update(chunk)\nactual = h.hexdigest()\nif actual != expected:\n    raise SystemExit(\"matrix hash mismatch \" + actual)\ntmp = \"{workspace_root}/{hash_tmp}\"\ndst = \"{workspace_root}/{hash_path}\"\nwith open(tmp, \"w\") as f:\n    f.write(actual)\nos.replace(tmp, dst)\nPY\nprintf {done_marker}; sleep 30'",
            command_count = case.command_count,
        );
        let started = lease.call_ok(
            catalog::SANDBOX_COMMAND_EXEC,
            json!({
                "cmd": command,
                "cwd": "/tmp",
                "remountable": true,
                "yield_time_ms": 500,
                "timeout_seconds": 120,
            }),
        )?;
        ensure!(
            as_str(&started, "status")? == "running",
            "matrix case {} requires command {command_index} to be running: {started}",
            case.name
        );
        let command_id = as_str(&started, "command_id")?.to_owned();
        wait_for_command_stdout_contains(&lease, &command_id, &ready_marker)?;
        command_ids.push((command_id, done_marker));
        expected_hashes.push(expected_hash);
        hash_paths.push(hash_path);
    }

    let body = (|| -> Result<()> {
        let remount = lease.call_ok(
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({"probe_path": &probe_path, "probe_content": &probe_content}),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "matrix case {} should verify remount before retarget: {remount}",
            case.name
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "remountable_commands")? == case.command_count as i64
                && as_i64(&remount, "process_count")? >= case.command_count as i64
                && as_i64(&remount, "quiesced_process_count")?
                    == as_i64(&remount, "process_count")?,
            "matrix case {} should quiesce every command process: {remount}",
            case.name
        );
        ensure!(
            as_i64(&remount, "after_layer_dirs")? <= 3
                && as_i64(&remount, "after_layer_dirs")? < as_i64(&remount, "before_layer_dirs")?,
            "matrix case {} should compact the mounted snapshot to bounded dirs: {remount}",
            case.name
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "matrix case {} should not pin the old workspace mount: {remount}",
            case.name
        );

        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({"path": &moved_path, "content": &moved_public, "overwrite": true}),
        )?;

        for (command_id, _) in &command_ids {
            lease.call_ok(
                catalog::SANDBOX_COMMAND_WRITE_STDIN,
                json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
            )?;
        }
        for (command_id, done_marker) in &command_ids {
            wait_for_command_stdout_contains(&lease, command_id, done_marker)?;
        }
        for ((hash_path, expected_hash), command_index) in
            hash_paths.iter().zip(expected_hashes.iter()).zip(0..)
        {
            let read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": hash_path}))?;
            ensure!(
                as_str(&read, "content")? == expected_hash,
                "matrix case {} command {command_index} should write expected hash after remount: {read}",
                case.name
            );
        }

        let isolated_read =
            lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &moved_path}))?;
        ensure!(
            as_str(&isolated_read, "content")? == moved_original,
            "matrix case {} isolated lease should not observe public head movement: {isolated_read}",
            case.name
        );
        let public_read = call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &moved_path}),
        )?;
        ensure!(
            as_str(&public_read, "content")? == moved_public,
            "matrix case {} public caller should see post-remount update: {public_read}",
            case.name
        );
        Ok(())
    })();

    for (command_id, _) in &command_ids {
        let _ = lease.call(
            catalog::SANDBOX_COMMAND_CANCEL,
            json!({"command_id": command_id}),
        );
    }
    let _ = wait_for_command_count(&lease, 0);
    let _ = lease.call(catalog::SANDBOX_ISOLATION_EXIT, json!({"grace_s": 0.1}));
    body
}

fn compact_remount_live_remount_pinned_history_matrix_case(
    case: RemountPinnedHistoryCase,
) -> Result<()> {
    ensure!(case.file_count > 0, "pinned-history case requires files");
    ensure!(
        case.historical_lease_count > 0,
        "pinned-history case requires historical leases"
    );
    ensure!(
        case.historical_lease_count <= 4,
        "pinned-history E2E config supports at most four historical leases plus one live lease"
    );
    ensure!(
        case.command_count > 0,
        "pinned-history case requires commands"
    );
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_networks(&lease);
    let suffix = e2e_test::unique_suffix();
    let public_caller = format!("remount-{}-public-{suffix}", case.name);
    let live_caller = format!("remount-{}-live-{suffix}", case.name);
    let tree_root = format!("compact-remount/{}-{suffix}", case.name);
    let manifest_path = format!("{tree_root}/manifest.txt");

    let mut paths = Vec::with_capacity(case.file_count);
    let mut manifest = String::new();
    let mut final_contents = vec![String::new(); case.file_count];
    for index in 0..case.file_count {
        let branch = index % case.branch_count.max(1);
        let shard = (index * case.path_stride) % 17;
        let path = format!("{tree_root}/history-{branch}/shard-{shard}/leaf-{index:03}.bin");
        manifest.push_str(&path);
        manifest.push('\n');
        paths.push(path);
    }

    let mut historical_snapshots = Vec::with_capacity(case.historical_lease_count);
    for generation in 0..=case.historical_lease_count {
        for rewrite in 0..case.rewrites_per_generation {
            for (index, path) in paths.iter().enumerate() {
                let content = versioned_payload(
                    &format!("{}-generation-{generation}-rewrite-{rewrite}", case.name),
                    index,
                    case.payload_bytes,
                );
                final_contents[index].clone_from(&content);
                call_ok_as(
                    &lease,
                    &public_caller,
                    catalog::SANDBOX_FILE_WRITE,
                    json!({"path": path, "content": &content, "overwrite": true}),
                )?;
            }
        }

        if generation < case.historical_lease_count {
            let caller = format!("remount-{}-history-{generation}-{suffix}", case.name);
            call_ok_as(&lease, &caller, catalog::SANDBOX_ISOLATION_ENTER, json!({}))?;
            let probe_index = generation % case.file_count;
            historical_snapshots.push((caller, final_contents.clone(), probe_index));
        }
    }

    call_ok_as(
        &lease,
        &public_caller,
        catalog::SANDBOX_FILE_WRITE,
        json!({"path": &manifest_path, "content": &manifest, "overwrite": true}),
    )?;

    let enter_live = call_ok_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_ISOLATION_ENTER,
        json!({}),
    )?;
    let workspace_root = as_str(&enter_live, "workspace_root")?.to_owned();
    let moved_path = paths[0].clone();
    let moved_original = final_contents[0].clone();
    let moved_public = versioned_payload(
        &format!("{}-public-after-remount", case.name),
        0,
        case.payload_bytes,
    );
    let probe_path = paths[case.file_count - 1].clone();
    let probe_content = final_contents[case.file_count - 1].clone();

    let mut command_ids = Vec::with_capacity(case.command_count);
    let mut expected_hashes = Vec::with_capacity(case.command_count);
    let mut hash_paths = Vec::with_capacity(case.command_count);
    for command_index in 0..case.command_count {
        let private_state = format!("{tree_root}/private-history-{command_index}-{suffix}.state");
        let hash_path = format!("{tree_root}/private-history-{command_index}-{suffix}.sha256");
        let hash_tmp = format!("{tree_root}/private-history-{command_index}-{suffix}.tmp");
        let private_content = format!("{}-private-history-{command_index}-{suffix}\n", case.name);
        let mut chunks = Vec::new();
        chunks.push(private_content.as_bytes());
        for (index, content) in final_contents.iter().enumerate() {
            if index % case.command_count == command_index {
                chunks.push(content.as_bytes());
            }
        }
        let expected_hash = sha256_hex(&chunks);
        let ready_marker = format!("PINNED_HISTORY_{command_index}_READY");
        let done_marker = format!("PINNED_HISTORY_{command_index}_DONE");
        let command = format!(
            "bash -lc 'set -euo pipefail; printf \"%s\" \"{private_content}\" > \"{workspace_root}/{private_state}\"; printf {ready_marker}; read -r _; python3 - <<\"PY\"\nimport hashlib, os\nroot = \"{workspace_root}\"\nmanifest = \"{workspace_root}/{manifest_path}\"\nprivate_state = \"{workspace_root}/{private_state}\"\nexpected = \"{expected_hash}\"\ncommand_index = {command_index}\ncommand_count = {command_count}\nwith open(manifest) as f:\n    paths = [line.strip() for line in f if line.strip()]\nh = hashlib.sha256()\nwith open(private_state, \"rb\") as f:\n    h.update(f.read())\nfor index, rel in enumerate(paths):\n    if index % command_count == command_index:\n        with open(os.path.join(root, rel), \"rb\") as f:\n            while True:\n                chunk = f.read(32768)\n                if not chunk:\n                    break\n                h.update(chunk)\nactual = h.hexdigest()\nif actual != expected:\n    raise SystemExit(\"pinned history hash mismatch \" + actual)\ntmp = \"{workspace_root}/{hash_tmp}\"\ndst = \"{workspace_root}/{hash_path}\"\nwith open(tmp, \"w\") as f:\n    f.write(actual)\nos.replace(tmp, dst)\nPY\nprintf {done_marker}; sleep 30'",
            command_count = case.command_count,
        );
        let started = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_COMMAND_EXEC,
            json!({
                "cmd": command,
                "cwd": "/tmp",
                "remountable": true,
                "yield_time_ms": 500,
                "timeout_seconds": 180,
            }),
        )?;
        ensure!(
            as_str(&started, "status")? == "running",
            "pinned-history case {} requires command {command_index} to be running: {started}",
            case.name
        );
        let command_id = as_str(&started, "command_id")?.to_owned();
        wait_for_command_stdout_contains_as(&lease, &live_caller, &command_id, &ready_marker)?;
        command_ids.push((command_id, done_marker));
        expected_hashes.push(expected_hash);
        hash_paths.push(hash_path);
    }

    let body = (|| -> Result<()> {
        let expected_active_leases = i64::try_from(case.historical_lease_count + 1)?;
        let _ = wait_for_active_leases(&lease, expected_active_leases)?;
        let remount = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT,
            json!({"probe_path": &probe_path, "probe_content": &probe_content}),
        )?;
        ensure!(
            remount.get("live_remount").and_then(Value::as_bool) == Some(true)
                && remount.get("mount_verified").and_then(Value::as_bool) == Some(true)
                && remount.get("lease_retargeted").and_then(Value::as_bool) == Some(true),
            "pinned-history case {} should verify live remount before retarget: {remount}",
            case.name
        );
        assert_lowerdir_proof_fields(&remount)?;
        ensure!(
            as_i64(&remount, "active_leases_after")? == (case.historical_lease_count + 1) as i64,
            "pinned-history case {} should keep all historical leases active: {remount}",
            case.name
        );
        ensure!(
            as_i64(&remount, "remountable_commands")? == case.command_count as i64
                && as_i64(&remount, "process_count")? >= case.command_count as i64
                && as_i64(&remount, "quiesced_process_count")?
                    == as_i64(&remount, "process_count")?,
            "pinned-history case {} should quiesce every command process: {remount}",
            case.name
        );
        ensure!(
            as_i64(&remount, "after_manifest_depth")? < as_i64(&remount, "before_manifest_depth")?,
            "pinned-history case {} should reduce the mounted lease manifest even when historical layers remain pinned: {remount}",
            case.name
        );
        ensure!(
            as_i64(&remount, "after_layer_dirs")? > 3,
            "pinned-history case {} should retain historical lowerdirs while leases are active: {remount}",
            case.name
        );
        ensure!(
            as_i64(&remount, "pinned_cwd_count")? == 0
                && as_i64(&remount, "pinned_fd_count")? == 0
                && as_i64(&remount, "pinned_mapped_file_count")? == 0,
            "pinned-history case {} commands should not pin the old mount: {remount}",
            case.name
        );

        call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_WRITE,
            json!({"path": &moved_path, "content": &moved_public, "overwrite": true}),
        )?;

        for (command_id, _) in &command_ids {
            call_ok_as(
                &lease,
                &live_caller,
                catalog::SANDBOX_COMMAND_WRITE_STDIN,
                json!({"command_id": command_id, "chars": "go\n", "yield_time_ms": 1500}),
            )?;
        }
        for (command_id, done_marker) in &command_ids {
            wait_for_command_stdout_contains_as(&lease, &live_caller, command_id, done_marker)?;
        }
        for ((hash_path, expected_hash), command_index) in
            hash_paths.iter().zip(expected_hashes.iter()).zip(0..)
        {
            let read = call_ok_as(
                &lease,
                &live_caller,
                catalog::SANDBOX_FILE_READ,
                json!({"path": hash_path}),
            )?;
            ensure!(
                as_str(&read, "content")? == expected_hash,
                "pinned-history case {} command {command_index} should hash the newest remounted snapshot: {read}",
                case.name
            );
        }

        for (caller, contents, probe_index) in &historical_snapshots {
            let read = call_ok_as(
                &lease,
                caller,
                catalog::SANDBOX_FILE_READ,
                json!({"path": &paths[*probe_index]}),
            )?;
            ensure!(
                as_str(&read, "content")? == contents[*probe_index],
                "historical caller {caller} should retain its pinned snapshot after newest remount: {read}"
            );
        }
        let live_read = call_ok_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &moved_path}),
        )?;
        ensure!(
            as_str(&live_read, "content")? == moved_original,
            "pinned-history case {} live lease should not observe public head movement: {live_read}",
            case.name
        );
        let public_read = call_ok_as(
            &lease,
            &public_caller,
            catalog::SANDBOX_FILE_READ,
            json!({"path": &moved_path}),
        )?;
        ensure!(
            as_str(&public_read, "content")? == moved_public,
            "pinned-history case {} public caller should see post-remount update: {public_read}",
            case.name
        );
        Ok(())
    })();

    for (command_id, _) in &command_ids {
        let _ = call_as(
            &lease,
            &live_caller,
            catalog::SANDBOX_COMMAND_CANCEL,
            json!({"command_id": command_id}),
        );
    }
    let _ = call_as(
        &lease,
        &live_caller,
        catalog::SANDBOX_ISOLATION_EXIT,
        json!({"grace_s": 0.1}),
    );
    for (caller, _, _) in historical_snapshots.iter().rev() {
        let _ = call_as(
            &lease,
            caller,
            catalog::SANDBOX_ISOLATION_EXIT,
            json!({"grace_s": 0.1}),
        );
    }
    let released = wait_for_active_leases(&lease, 0);
    body?;
    released?;
    Ok(())
}

fn assert_lowerdir_proof_fields(remount: &Value) -> Result<()> {
    ensure!(
        as_i64(remount, "remount_mountinfo_lowerdir_expected_count")?
            == as_i64(remount, "remounted_layer_count")?,
        "remount should report expected lowerdir count from the requested layer list: {remount}"
    );
    ensure!(
        remount
            .get("remount_mountinfo_lowerdir_count_matched")
            .and_then(Value::as_bool)
            == Some(true),
        "visible mountinfo lowerdir count must match expected before lease retarget: {remount}"
    );
    ensure!(
        remount
            .get("remount_mountinfo_lowerdir_verified")
            .and_then(Value::as_bool)
            == Some(true),
        "visible mountinfo lowerdirs must exactly match expected before lease retarget: {remount}"
    );
    Ok(())
}

fn assert_blocked_remount_reports_pressure_only(fields: &Value) -> Result<()> {
    ensure!(
        !as_bool(fields, "fallback_compaction_enabled")?,
        "blocked remount must not run hard-protection fallback compaction: {fields}"
    );
    ensure!(
        as_str(fields, "fallback_compaction_policy")? == "disabled_report_only",
        "blocked remount should report pressure only: {fields}"
    );
    ensure!(
        as_i64(fields, "fallback_checkpoint_count")? == 0
            && as_i64(fields, "fallback_compacted_layers")? == 0
            && as_i64(fields, "fallback_skipped_delta_intervals")? == 0,
        "blocked remount must expose zero fallback compaction counts: {fields}"
    );
    ensure!(
        as_i64(fields, "after_manifest_depth")? == as_i64(fields, "before_manifest_depth")?
            && as_i64(fields, "after_layer_dirs")? == as_i64(fields, "before_layer_dirs")?
            && as_i64(fields, "after_storage_bytes")? == as_i64(fields, "before_storage_bytes")?,
        "blocked remount must leave LayerStack metrics unchanged: {fields}"
    );
    Ok(())
}

fn assert_blocked_trace_reports_pressure_only(record: &trace::TraceRecord) -> Result<()> {
    ensure!(
        has_trace_event(record, "layer_stack", "lease_remount_blocked", |details| {
            details["fallback_compaction_enabled"] == false
                && details["fallback_compaction_policy"] == "disabled_report_only"
                && details["fallback_checkpoint_count"]
                    .as_i64()
                    .unwrap_or_default()
                    == 0
                && details["fallback_compacted_layers"]
                    .as_i64()
                    .unwrap_or_default()
                    == 0
                && details["fallback_skipped_delta_intervals"]
                    .as_i64()
                    .unwrap_or_default()
                    == 0
        }),
        "blocked remount trace must report disabled fallback compaction: {record:?}"
    );
    Ok(())
}

fn sha256_hex(chunks: &[&[u8]]) -> String {
    let mut hasher = Sha256::new();
    for chunk in chunks {
        hasher.update(chunk);
    }
    format!("{:x}", hasher.finalize())
}

fn call_as(
    lease: &e2e_test::NodeLease<'_>,
    caller_id: &str,
    op: &str,
    mut args: Value,
) -> Result<Value> {
    args.as_object_mut()
        .context("call_as args must be a JSON object")?
        .insert("caller_id".to_owned(), json!(caller_id));
    lease.call(op, args)
}

fn call_ok_as(
    lease: &e2e_test::NodeLease<'_>,
    caller_id: &str,
    op: &str,
    mut args: Value,
) -> Result<Value> {
    args.as_object_mut()
        .context("call_ok_as args must be a JSON object")?
        .insert("caller_id".to_owned(), json!(caller_id));
    lease.call_ok(op, args)
}

fn wait_for_command_stdout_contains_as(
    lease: &e2e_test::NodeLease<'_>,
    caller_id: &str,
    command_id: &str,
    needle: &str,
) -> Result<()> {
    wait_for_command_stdout_contains_as_timeout(
        lease,
        caller_id,
        command_id,
        needle,
        std::time::Duration::from_secs(15),
    )
}

fn wait_for_command_stdout_contains_as_timeout(
    lease: &e2e_test::NodeLease<'_>,
    caller_id: &str,
    command_id: &str,
    needle: &str,
    timeout: std::time::Duration,
) -> Result<()> {
    let deadline = std::time::Instant::now() + timeout;
    loop {
        let progress = call_ok_as(
            lease,
            caller_id,
            catalog::SANDBOX_COMMAND_POLL,
            json!({"command_id": command_id, "last_n_lines": 50}),
        )?;
        let stdout = progress
            .get("stdout")
            .or_else(|| {
                progress
                    .get("output")
                    .and_then(|output| output.get("stdout"))
            })
            .and_then(Value::as_str)
            .unwrap_or_default();
        if stdout.contains(needle) {
            return Ok(());
        }
        if std::time::Instant::now() >= deadline {
            anyhow::bail!("command stdout did not surface {needle:?} before deadline: {progress}");
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
}

fn versioned_payload(label: &str, index: usize, bytes: usize) -> String {
    let header = format!("{label}-{index}\n");
    let fill = char::from(b'A' + (index % 26) as u8);
    let body_len = bytes.saturating_sub(header.len());
    format!("{header}{}", fill.to_string().repeat(body_len))
}
