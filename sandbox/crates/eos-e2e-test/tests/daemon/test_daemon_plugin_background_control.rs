//! Dynamic plugin ops participate in the generic background invocation registry.

use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use eos_e2e_test::{unique_suffix, NodeLease};
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::support::{as_bool, as_i64, live_pool_or_skip};

#[test]
fn background_plugin_operation_control() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let digest = format!("digest-{}", unique_suffix().replace('-', "_"));
    ensure_slow_plugin_service(&lease, &digest)?;

    let invocation_id = format!("daemon-plugin-bg-{}", unique_suffix());
    let handle = spawn_background_plugin_query(&lease, &invocation_id);
    wait_for_inflight_count(&lease, 1, Duration::from_secs(4))?;

    let heartbeat = lease.call_ok(
        ops::API_V1_HEARTBEAT,
        json!({"invocation_ids": [invocation_id.clone(), "not-live"]}),
    )?;
    assert_eq!(
        as_i64(&heartbeat, "touched")?,
        1,
        "background plugin invocation should be heartbeat-visible: {heartbeat}"
    );

    let cancel = lease.call_ok(
        ops::API_V1_CANCEL,
        json!({"invocation_id": invocation_id.clone()}),
    )?;
    assert!(
        as_bool(&cancel, "cancelled")?,
        "background plugin invocation should be found by cancel: {cancel}"
    );

    let _ = handle
        .join()
        .map_err(|_| anyhow::anyhow!("background plugin thread panicked"))?;
    wait_for_inflight_count(&lease, 0, Duration::from_secs(4))?;
    Ok(())
}

fn ensure_slow_plugin_service(lease: &NodeLease<'_>, digest: &str) -> Result<()> {
    let manifest = slow_plugin_manifest(digest);
    let warm = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": manifest,
            "start_services": true,
        }),
    )?;
    assert_eq!(warm["needs_upload"], true, "{warm}");
    let staged = stage_slow_plugin_package(lease, digest)?;
    let cold = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": slow_plugin_manifest(digest),
            "staged_package_root": staged,
            "start_services": true,
        }),
    )?;
    assert_eq!(cold["success"], true, "{cold}");
    assert_eq!(cold["service_processes_started"], true, "{cold}");
    assert_eq!(
        cold["connected_ppc_routes"],
        json!(["plugin.daemonplug.query"]),
        "{cold}"
    );
    Ok(())
}

fn spawn_background_plugin_query(
    lease: &NodeLease<'_>,
    invocation_id: &str,
) -> JoinHandle<Result<Value>> {
    let client = lease.client().clone();
    let root = lease.root().to_owned();
    let caller_id = lease.caller_id().to_owned();
    let invocation_id = invocation_id.to_owned();
    thread::spawn(move || {
        Ok(client.request(
            "plugin.daemonplug.query",
            &invocation_id,
            &json!({
                "layer_stack_root": root,
                "caller_id": caller_id,
                "background": true,
                "sleep_s": 4,
                "request": "daemon-background-control"
            }),
        )?)
    })
}

fn slow_plugin_manifest(digest: &str) -> Value {
    json!({
        "plugin_id": "daemonplug",
        "plugin_version": "0.1.0",
        "plugin_digest": digest,
        "package": {
            "runtime_dir": "runtime",
            "dependency_scope": "package_digest"
        },
        "services": [{
            "service_id": "worker",
            "service_profile_digest": format!("slow-profile-{digest}"),
            "service_mode": "workspace_snapshot_refresh",
            "refresh_strategy": "remount_workspace_and_notify",
            "command": ["./server.py"],
            "working_dir": "runtime",
            "ppc_protocol_version": 1
        }],
        "operations": [{
            "op_name": "query",
            "intent": "read_only",
            "service_id": "worker",
            "timeout_ms": 12000
        }]
    })
}

fn stage_slow_plugin_package(lease: &NodeLease<'_>, digest: &str) -> Result<String> {
    let staged = format!("/eos/scratch/uploads/plugins/daemonplug/{digest}/upload-1/package");
    let cmd = format!(
        r#"set -eu
pkg="{staged}"
rm -rf "$pkg"
mkdir -p "$pkg/runtime"
printf '%s' "{digest}" > "$pkg/.package-sha256"
printf '{{}}' > "$pkg/sandbox-plugin.json"
cat > "$pkg/runtime/server.py" <<'PY'
#!/usr/bin/env python3
import json
import os
import socket
import time

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["EOS_PLUGIN_PPC_SOCKET"])
buffer = b""
manifest_key = "initial"

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
        manifest_key = body.get("target_manifest_key") or body.get("manifest_key") or manifest_key
        send(request["invocation_id"], {{"manifest_key": manifest_key, "accepted": True}})
        continue
    time.sleep(float(body.get("sleep_s", 0)))
    send(request["invocation_id"], {{
        "success": True,
        "manifest_key": manifest_key,
        "request": body,
    }})
PY
chmod +x "$pkg/runtime/server.py"
"#
    );
    // Stage the package on the real container filesystem via the daemon
    // container directly: a model-facing `exec_command` runs in the fresh
    // namespace where `/eos` is masked (an empty read-only tmpfs), so it cannot
    // write the upload tree the daemon reads back. Container exec runs in the
    // container's main namespace where `/eos/scratch` is the real writable tmpfs.
    lease
        .container()
        .exec(&["sh", "-lc", &cmd])
        .context("stage slow plugin package")?;
    Ok(staged)
}

fn wait_for_inflight_count(
    lease: &NodeLease<'_>,
    expected: i64,
    timeout: Duration,
) -> Result<Value> {
    let deadline = Instant::now() + timeout;
    loop {
        let count = lease.call_ok(ops::API_V1_INFLIGHT_COUNT, json!({}))?;
        if as_i64(&count, "count")? == expected {
            return Ok(count);
        }
        if Instant::now() >= deadline {
            bail!("inflight_count did not reach {expected}: {count}");
        }
        thread::sleep(Duration::from_millis(50));
    }
}
