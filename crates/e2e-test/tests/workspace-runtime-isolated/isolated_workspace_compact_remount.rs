use anyhow::{ensure, Context, Result};
use protocol::catalog;
use serde_json::{json, Value};

use crate::support::{
    as_bool, as_i64, as_str, envelope_error_kind, has_trace_event, live_pool_or_skip,
    reset_isolated_workspaces, trace_record, wait_for_command_count,
    wait_for_command_stdout_contains,
};

#[test]
fn compact_remount_open_isolated_workspace_reclaims_old_lower_chain() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_workspaces(&lease);
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
    reset_isolated_workspaces(&lease);

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
    reset_isolated_workspaces(&lease);
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
    reset_isolated_workspaces(&lease);
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
    reset_isolated_workspaces(&lease);
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
    reset_isolated_workspaces(&lease);
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
fn compact_remount_reports_not_open_for_ephemeral_caller() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    reset_isolated_workspaces(&lease);

    let response = lease.call(catalog::SANDBOX_ISOLATION_TEST_COMPACT_REMOUNT, json!({}))?;
    let error = response
        .get("error")
        .or_else(|| response.get("fault"))
        .context("expected rejected operation error")?;
    ensure!(
        as_str(error, "kind")? == "not_open",
        "ephemeral/public callers have no mounted lease to remount: {response}"
    );
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
