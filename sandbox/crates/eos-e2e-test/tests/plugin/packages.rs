use std::thread;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use eos_e2e_test::unique_suffix;
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::live_pool_or_skip;

#[test]
fn host_ensure_plugin_package_installs_generic_package() -> Result<()> {
    generic_package_installs_and_sets_up()
}

#[test]
fn generic_package_installs_and_sets_up() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let digest = format!("digest-{}", unique_suffix().replace('-', "_"));
    let setup_digest = format!("setup-{digest}");
    let staged = stage_generic_package(&lease, &digest)?;

    let warm = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": manifest(&digest, &setup_digest),
        }),
    )?;
    assert_eq!(
        warm["needs_upload"], true,
        "missing package should request upload: {warm}"
    );

    let cold = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": manifest(&digest, &setup_digest),
            "staged_package_root": staged,
        }),
    )?;
    assert_eq!(cold["success"], true);
    assert_eq!(cold["package"]["package_published"], true);
    assert_eq!(cold["package"]["setup_ran"], true);

    assert_container_path(
        &lease,
        &format!("/eos/runtime/plugins/catalog/generic/{digest}/.package-sha256"),
    )?;
    assert_container_path(
        &lease,
        &format!("/eos/runtime/plugins/catalog/generic/{digest}/.setup-sha256"),
    )?;
    assert_container_path(
        &lease,
        &format!("/eos/runtime/packages/generic/{digest}/cache/setup.txt"),
    )?;
    assert_container_path(
        &lease,
        &format!("/eos/scratch/setup/generic/{digest}/tmp/setup.tmp"),
    )?;
    Ok(())
}

#[test]
fn generic_package_reensure_is_idempotent() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let digest = format!("digest-{}", unique_suffix().replace('-', "_"));
    let setup_digest = format!("setup-{digest}");
    let staged = stage_generic_package(&lease, &digest)?;

    let _ = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": manifest(&digest, &setup_digest),
            "staged_package_root": staged,
        }),
    )?;
    let warm = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": manifest(&digest, &setup_digest),
        }),
    )?;
    assert_eq!(warm["success"], true);
    assert_eq!(warm["package"]["needs_upload"], false);
    assert_eq!(warm["package"]["setup_ran"], false);
    let count = read_container_file(
        &lease,
        &format!("/eos/runtime/packages/generic/{digest}/cache/setup-count"),
    )?;
    assert_eq!(count.trim(), "1");
    Ok(())
}

#[test]
fn generic_plugin_dispatch_roundtrip() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let digest = format!("digest-{}", unique_suffix().replace('-', "_"));
    let setup_digest = format!("setup-{digest}");
    ensure_generic_service_package(&lease, &digest, &setup_digest)?;

    let response = lease.call_ok(
        "plugin.generic.query",
        json!({"path": "missing.txt", "request": "roundtrip"}),
    )?;
    assert_eq!(response["success"], true);
    assert_eq!(response["op"], "plugin.generic.query");
    assert_eq!(response["request"]["request"], "roundtrip");
    assert_eq!(
        response["package_root"],
        format!("/eos/runtime/plugins/catalog/generic/{digest}")
    );
    assert_eq!(
        response["dependency_root"],
        format!("/eos/runtime/packages/generic/{digest}")
    );
    Ok(())
}

#[test]
fn generic_plugin_refreshes_after_workspace_edit() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let digest = format!("digest-{}", unique_suffix().replace('-', "_"));
    let setup_digest = format!("setup-{digest}");
    ensure_generic_service_package(&lease, &digest, &setup_digest)?;

    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "phase5/refresh.txt", "content": "after-refresh\n", "overwrite": true}),
    )?;
    let response = lease.call_ok(
        "plugin.generic.query",
        json!({"path": "phase5/refresh.txt"}),
    )?;
    assert_eq!(response["success"], true);
    assert_eq!(response["content"], "after-refresh\n");
    assert!(
        response["refresh_events"].as_u64().unwrap_or_default() > 0,
        "dispatch after write should refresh service workspace: {response}"
    );

    let status = lease.call_ok(ops::API_PLUGIN_STATUS, json!({}))?;
    assert!(
        status["loaded_plugins"][0]["services"][0]["refresh_count"]
            .as_u64()
            .unwrap_or_default()
            > 0,
        "status should record service refresh: {status}"
    );
    Ok(())
}

#[test]
fn service_health_probe_reports_connected_service() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let digest = format!("digest-{}", unique_suffix().replace('-', "_"));
    let setup_digest = format!("setup-{digest}");
    ensure_generic_service_package(&lease, &digest, &setup_digest)?;

    // probe_services drives a live PPC health round-trip to the worker, which the
    // generic server already answers; the success path is otherwise untested.
    let status = lease.call_ok(
        ops::API_PLUGIN_STATUS,
        json!({"probe_services": true, "probe_timeout_ms": 5000}),
    )?;
    let health = status
        .get("service_health")
        .and_then(Value::as_array)
        .context("status.service_health array")?;
    assert!(
        !health.is_empty(),
        "probe_services must populate service_health: {status}"
    );
    assert_eq!(
        health[0]["success"], json!(true),
        "the service health probe must succeed: {status}"
    );
    assert_eq!(
        health[0]["accepted"], json!(true),
        "the worker must accept the health probe: {status}"
    );
    Ok(())
}

#[test]
fn restart_service_strategy_restarts_on_workspace_edit() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let digest = format!("digest-{}", unique_suffix().replace('-', "_"));
    let setup_digest = format!("setup-{digest}");

    // A different update policy than the covered remount_workspace_and_notify:
    // a workspace edit restarts (kills + respawns) the service process.
    let warm = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": service_manifest_with_strategy(&digest, &setup_digest, "restart_service"),
            "start_services": true,
        }),
    )?;
    assert_eq!(warm["needs_upload"], json!(true), "{warm}");
    let staged = stage_generic_service_package(&lease, &digest)?;
    let cold = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": service_manifest_with_strategy(&digest, &setup_digest, "restart_service"),
            "staged_package_root": staged,
            "start_services": true,
        }),
    )?;
    assert_eq!(cold["service_processes_started"], json!(true), "{cold}");
    assert_eq!(restart_count(&lease.call_ok(ops::API_PLUGIN_STATUS, json!({}))?), 0);

    // Advance the workspace manifest, then a dispatch forces the refresh, which
    // for restart_service is a process restart (used only to trigger; may defer).
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": "restart/edit.txt", "content": "after-restart\n", "overwrite": true}),
    )?;
    let _ = lease.call("plugin.generic.query", json!({"path": "restart/edit.txt"}));

    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        let status = lease.call_ok(ops::API_PLUGIN_STATUS, json!({}))?;
        if restart_count(&status) >= 1 {
            // A restart bumps restart_count, NOT refresh_count (that is the remount
            // policy's signal) — the discriminating observable between policies.
            assert_eq!(
                status["loaded_plugins"][0]["services"][0]["refresh_count"]
                    .as_i64()
                    .unwrap_or(-1),
                0,
                "restart_service must restart, not remount: {status}"
            );
            return Ok(());
        }
        if Instant::now() >= deadline {
            bail!("restart_service did not restart the worker after a workspace edit");
        }
        let _ = lease.call("plugin.generic.query", json!({"path": "restart/edit.txt"}));
        thread::sleep(Duration::from_millis(150));
    }
}

fn restart_count(status: &Value) -> i64 {
    status["loaded_plugins"][0]["services"][0]["restart_count"]
        .as_i64()
        .unwrap_or(-1)
}

fn manifest(digest: &str, setup_digest: &str) -> Value {
    json!({
        "plugin_id": "generic",
        "plugin_version": "0.1.0",
        "plugin_digest": digest,
        "package": {
            "runtime_dir": "runtime",
            "dependency_scope": "package_digest"
        },
        "setup": {
            "command": ["./setup.sh"],
            "working_dir": ".",
            "setup_marker_digest": setup_digest,
            "timeout_ms": 30000
        },
        "services": [],
        "operations": []
    })
}

fn service_manifest(digest: &str, setup_digest: &str) -> Value {
    service_manifest_with_strategy(digest, setup_digest, "remount_workspace_and_notify")
}

fn service_manifest_with_strategy(
    digest: &str,
    setup_digest: &str,
    refresh_strategy: &str,
) -> Value {
    json!({
        "plugin_id": "generic",
        "plugin_version": "0.1.0",
        "plugin_digest": digest,
        "package": {
            "runtime_dir": "runtime",
            "dependency_scope": "package_digest"
        },
        "setup": {
            "command": ["./setup.sh"],
            "working_dir": ".",
            "setup_marker_digest": setup_digest,
            "timeout_ms": 30000
        },
        "services": [{
            "service_id": "worker",
            "service_profile_digest": format!("profile-{digest}"),
            "service_mode": "workspace_snapshot_refresh",
            "refresh_strategy": refresh_strategy,
            "command": ["./server.py"],
            "working_dir": "runtime",
            "ppc_protocol_version": 1
        }],
        "operations": [{
            "op_name": "query",
            "intent": "read_only",
            "service_id": "worker",
            "timeout_ms": 5000
        }]
    })
}

fn stage_generic_package(lease: &eos_e2e_test::NodeLease<'_>, digest: &str) -> Result<String> {
    let staged = format!("/eos/scratch/uploads/plugins/generic/{digest}/upload-1/package");
    let cmd = format!(
        r#"set -eu
pkg="{staged}"
rm -rf "$pkg"
mkdir -p "$pkg/runtime"
printf '%s' "{digest}" > "$pkg/.package-sha256"
printf '{{}}' > "$pkg/sandbox-plugin.json"
printf '#!/bin/sh\n' > "$pkg/runtime/server.sh"
cat > "$pkg/setup.sh" <<'SH'
#!/bin/sh
set -eu
count_file="$EOS_PLUGIN_DEPENDENCY_ROOT/cache/setup-count"
count=0
if [ -f "$count_file" ]; then count="$(cat "$count_file")"; fi
count=$((count + 1))
printf '%s' "$count" > "$count_file"
printf setup-ok > "$EOS_PLUGIN_DEPENDENCY_ROOT/cache/setup.txt"
printf tmp-ok > "$TMPDIR/setup.tmp"
SH
chmod +x "$pkg/setup.sh"
"#
    );
    let response = lease.call_ok(ops::API_V1_EXEC_COMMAND, json!({"cmd": cmd}))?;
    if response.get("status").and_then(Value::as_str) == Some("error") {
        anyhow::bail!("package staging command failed: {response}");
    }
    Ok(staged)
}

fn ensure_generic_service_package(
    lease: &eos_e2e_test::NodeLease<'_>,
    digest: &str,
    setup_digest: &str,
) -> Result<Value> {
    let manifest = service_manifest(digest, setup_digest);
    let warm = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": manifest,
            "start_services": true,
        }),
    )?;
    assert_eq!(warm["needs_upload"], true);
    let staged = stage_generic_service_package(lease, digest)?;
    let cold = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": service_manifest(digest, setup_digest),
            "staged_package_root": staged,
            "start_services": true,
        }),
    )?;
    assert_eq!(cold["success"], true);
    assert_eq!(cold["service_processes_started"], true);
    assert_eq!(
        cold["connected_ppc_routes"],
        json!(["plugin.generic.query"])
    );
    Ok(cold)
}

fn stage_generic_service_package(
    lease: &eos_e2e_test::NodeLease<'_>,
    digest: &str,
) -> Result<String> {
    let staged = format!("/eos/scratch/uploads/plugins/generic/{digest}/upload-1/package");
    let cmd = format!(
        r#"set -eu
pkg="{staged}"
rm -rf "$pkg"
mkdir -p "$pkg/runtime"
printf '%s' "{digest}" > "$pkg/.package-sha256"
printf '{{}}' > "$pkg/sandbox-plugin.json"
cat > "$pkg/setup.sh" <<'SH'
#!/bin/sh
set -eu
printf setup-ok > "$EOS_PLUGIN_DEPENDENCY_ROOT/cache/service-setup.txt"
SH
chmod +x "$pkg/setup.sh"
cat > "$pkg/runtime/server.py" <<'PY'
#!/usr/bin/env python3
import json
import os
import socket

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["EOS_PLUGIN_PPC_SOCKET"])
buffer = b""
manifest_key = "initial"
refresh_events = 0

def send(message_id, body):
    frame = {{
        "op": "reply",
        "invocation_id": message_id,
        "args": {{"direction": "reply", "body": json.dumps(body, separators=(",", ":"))}},
    }}
    sock.sendall(json.dumps(frame, separators=(",", ":")).encode() + b"\n")

while True:
    while b"\n" not in buffer:
        chunk = sock.recv(65536)
        if not chunk:
            raise SystemExit(0)
        buffer += chunk
    line, buffer = buffer.split(b"\n", 1)
    request = json.loads(line.decode())
    body = json.loads(request["args"]["body"])
    if request["op"] == "daemon.workspace_snapshot_refresh":
        key = body.get("target_manifest_key") or body.get("manifest_key") or manifest_key
        manifest_key = key
        refresh_events += 1
        send(request["invocation_id"], {{"manifest_key": manifest_key, "accepted": True}})
        continue

    path = body.get("path")
    content = None
    if path:
        try:
            with open(os.path.join(os.environ["EOS_PLUGIN_WORKSPACE_ROOT"], path), "r", encoding="utf-8") as handle:
                content = handle.read()
        except FileNotFoundError:
            content = None
    send(request["invocation_id"], {{
        "success": True,
        "op": request["op"],
        "request": body,
        "content": content,
        "manifest_key": manifest_key,
        "refresh_events": refresh_events,
        "package_root": os.environ["EOS_PLUGIN_PACKAGE_ROOT"],
        "dependency_root": os.environ["EOS_PLUGIN_DEPENDENCY_ROOT"],
    }})
PY
chmod +x "$pkg/runtime/server.py"
"#
    );
    let response = lease.call_ok(ops::API_V1_EXEC_COMMAND, json!({"cmd": cmd}))?;
    if response.get("status").and_then(Value::as_str) == Some("error") {
        anyhow::bail!("service package staging command failed: {response}");
    }
    Ok(staged)
}

fn assert_container_path(lease: &eos_e2e_test::NodeLease<'_>, path: &str) -> Result<()> {
    let response = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({"cmd": format!("test -f {}", shell_quote(path))}),
    )?;
    if response.get("status").and_then(Value::as_str) == Some("error") {
        anyhow::bail!("expected container path {path}: {response}");
    }
    Ok(())
}

fn read_container_file(lease: &eos_e2e_test::NodeLease<'_>, path: &str) -> Result<String> {
    let response = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({"cmd": format!("cat {}", shell_quote(path))}),
    )?;
    response
        .get("output")
        .and_then(|output| output.get("stdout"))
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .with_context(|| format!("stdout missing in {response}"))
}

fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\"'\"'"))
}
