#[path = "common/mod.rs"]
mod common;

use anyhow::{Context, Result};
use eos_protocol::ops;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::common::live_pool_or_skip;

const PLUGIN_MD: &str = r##"# LSP Plugin Package

Live E2E fixture for the normal LSP package lifecycle. The catalog source owns
the production package; this fixture mirrors the daemon-facing package shape so
the sandbox harness does not depend on catalog crate source paths.
"##;
const SANDBOX_PLUGIN_JSON: &str = r##"{
  "plugin_id": "lsp",
  "plugin_version": "0.1.0",
  "service": "pyright",
  "entrypoint": "runtime/ppc_service.py"
}
"##;
const SETUP_SH: &str = r##"#!/bin/sh
set -eu

dep="${EOS_PLUGIN_DEPENDENCY_ROOT:?}"
node_root="$dep/node22"
node_bin="$node_root/bin/node"
pyright_root="$node_root/lib/node_modules/pyright"

mkdir -p "$node_root/bin" "$pyright_root" "$dep/pyright" "$dep/npm-cache"
cat > "$node_bin" <<'NODE'
#!/bin/sh
set -eu
if [ "$#" -gt 0 ] && [ -f "$1" ]; then
    script="$1"
    shift
    exec python3 "$script" "$@"
fi
printf '%s\n' "node22 package shim"
NODE
chmod +x "$node_bin"

cat > "$pyright_root/langserver.index.js" <<'PYRIGHT'
#!/usr/bin/env python3
import sys
for line in sys.stdin:
    if not line:
        break
PYRIGHT
chmod +x "$pyright_root/langserver.index.js"
"##;
const PPC_SERVICE: &str = r##"#!/usr/bin/env python3
import ast
import json
import os
import socket
from pathlib import Path

workspace = Path(os.environ["EOS_PLUGIN_WORKSPACE_ROOT"]).resolve()
dependency_root = Path(os.environ["EOS_PLUGIN_DEPENDENCY_ROOT"])
package_root = os.environ["EOS_PLUGIN_PACKAGE_ROOT"]

def pyright_argv():
    return [
        str(dependency_root / "node22" / "bin" / "node"),
        str(dependency_root / "node22" / "lib" / "node_modules" / "pyright" / "langserver.index.js"),
        "--stdio",
    ]

def send(sock, invocation_id, body):
    frame = {
        "op": "reply",
        "invocation_id": invocation_id,
        "args": {"direction": "reply", "body": json.dumps(body, separators=(",", ":"))},
    }
    sock.sendall(json.dumps(frame, separators=(",", ":")).encode() + b"\n")

def decode_body(request):
    raw = request.get("args", {}).get("body", "{}")
    return json.loads(raw) if isinstance(raw, str) else (raw or {})

def resolve_workspace_path(path):
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve()
    resolved.relative_to(workspace)
    return resolved

def symbol_range(node):
    return {
        "start": {"line": max(getattr(node, "lineno", 1) - 1, 0), "character": getattr(node, "col_offset", 0)},
        "end": {"line": max(getattr(node, "end_lineno", getattr(node, "lineno", 1)) - 1, 0), "character": getattr(node, "end_col_offset", getattr(node, "col_offset", 0))},
    }

def query_symbols(body):
    path = resolve_workspace_path(body["file_path"])
    query = str(body.get("query") or "").lower()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    symbols = []
    for node in ast.walk(tree):
        name = getattr(node, "name", None)
        kind = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "function"
        elif isinstance(node, ast.ClassDef):
            kind = "class"
        if name and kind and (not query or query in name.lower()):
            range_value = symbol_range(node)
            symbols.append({
                "name": name,
                "kind": kind,
                "file_path": str(path.relative_to(workspace)),
                "range": range_value,
                "selection_range": range_value,
            })
    return {
        "success": True,
        "symbols": symbols,
        "package_root": package_root,
        "dependency_root": str(dependency_root),
        "pyright_argv": pyright_argv(),
        "node_exists": (dependency_root / "node22" / "bin" / "node").exists(),
        "langserver_exists": (dependency_root / "node22" / "lib" / "node_modules" / "pyright" / "langserver.index.js").exists(),
    }

def main():
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(os.environ["EOS_PLUGIN_PPC_SOCKET"])
    buffer = b""
    manifest_key = "initial"
    while True:
        while b"\n" not in buffer:
            chunk = sock.recv(65536)
            if not chunk:
                return 0
            buffer += chunk
        line, buffer = buffer.split(b"\n", 1)
        request = json.loads(line.decode())
        body = decode_body(request)
        if request.get("op") == "daemon.workspace_snapshot_refresh":
            manifest_key = body.get("target_manifest_key") or body.get("manifest_key") or manifest_key
            send(sock, request["invocation_id"], {"accepted": True, "manifest_key": manifest_key})
            continue
        try:
            response = query_symbols(body)
            response["manifest_key"] = manifest_key
        except Exception as exc:
            response = {"success": False, "error": str(exc), "manifest_key": manifest_key}
        send(sock, request["invocation_id"], response)

if __name__ == "__main__":
    raise SystemExit(main())
"##;

#[derive(Clone, Copy)]
struct PackageFile {
    path: &'static str,
    contents: &'static str,
    mode: u32,
}

const PACKAGE_FILES: &[PackageFile] = &[
    PackageFile {
        path: "plugin.md",
        contents: PLUGIN_MD,
        mode: 0o644,
    },
    PackageFile {
        path: "runtime/ppc_service.py",
        contents: PPC_SERVICE,
        mode: 0o755,
    },
    PackageFile {
        path: "sandbox-plugin.json",
        contents: SANDBOX_PLUGIN_JSON,
        mode: 0o644,
    },
    PackageFile {
        path: "setup.sh",
        contents: SETUP_SH,
        mode: 0o755,
    },
];

#[test]
fn lsp_package_uses_generic_lifecycle_and_dispatches_symbols() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let digest = package_digest(PACKAGE_FILES);
    let setup_digest = format!("setup-{digest}");
    let expected_dependency_root = format!("/eos/runtime/packages/lsp/{digest}");

    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({
            "path": "phase7_lsp/sample.py",
            "content": "class PhaseSeven:\n    pass\n\ndef live_symbol(value):\n    return value\n",
            "overwrite": true
        }),
    )?;

    let warm = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": manifest(&digest, &setup_digest),
            "start_services": true,
        }),
    )?;
    assert_eq!(
        warm["needs_upload"], true,
        "missing LSP package should request upload: {warm}"
    );

    let staged = stage_lsp_package(&lease, &digest)?;
    let cold = lease.call_ok(
        ops::API_PLUGIN_ENSURE,
        json!({
            "workspace_root": lease.workspace_root(),
            "manifest": manifest(&digest, &setup_digest),
            "staged_package_root": staged,
            "start_services": true,
        }),
    )?;
    assert_eq!(cold["success"], true);
    assert_eq!(cold["package"]["package_published"], true);
    assert_eq!(cold["package"]["setup_ran"], true);
    assert_eq!(cold["service_processes_started"], true);
    assert_eq!(
        cold["connected_ppc_routes"],
        json!(["plugin.lsp.query_symbols"])
    );

    assert_container_path(
        &lease,
        &format!("/eos/runtime/plugins/catalog/lsp/{digest}/setup.sh"),
    )?;
    assert_container_path(
        &lease,
        &format!("{expected_dependency_root}/node22/bin/node"),
    )?;
    assert_container_path(
        &lease,
        &format!("{expected_dependency_root}/node22/lib/node_modules/pyright/langserver.index.js"),
    )?;
    assert_container_dir(&lease, &format!("{expected_dependency_root}/npm-cache"))?;
    assert_container_dir(&lease, &format!("{expected_dependency_root}/pyright"))?;

    let response = lease.call_ok(
        "plugin.lsp.query_symbols",
        json!({
            "query": "live_symbol",
            "file_path": "phase7_lsp/sample.py"
        }),
    )?;
    assert_eq!(response["success"], true);
    assert_eq!(response["dependency_root"], expected_dependency_root);
    assert_eq!(
        response["pyright_argv"][0],
        format!("{expected_dependency_root}/node22/bin/node")
    );
    assert_eq!(
        response["pyright_argv"][1],
        format!("{expected_dependency_root}/node22/lib/node_modules/pyright/langserver.index.js")
    );
    assert!(response["node_exists"].as_bool().unwrap_or_default());
    assert!(response["langserver_exists"].as_bool().unwrap_or_default());
    let symbols = response["symbols"]
        .as_array()
        .context("symbols missing from LSP response")?;
    assert!(
        symbols.iter().any(|symbol| symbol["name"] == "live_symbol"),
        "symbol response should include live_symbol: {response}"
    );
    Ok(())
}

fn manifest(digest: &str, setup_digest: &str) -> Value {
    json!({
        "plugin_id": "lsp",
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
            "timeout_ms": 60000
        },
        "services": [{
            "service_id": "pyright",
            "service_profile_digest": format!("phase7-lsp-pyright-{digest}"),
            "service_mode": "workspace_snapshot_refresh",
            "refresh_strategy": "remount_workspace_and_notify",
            "command": ["./ppc_service.py"],
            "working_dir": "runtime",
            "ppc_protocol_version": 1
        }],
        "operations": [{
            "op_name": "query_symbols",
            "intent": "read_only",
            "auto_workspace_overlay": true,
            "service_id": "pyright",
            "timeout_ms": 150000
        }]
    })
}

fn package_digest(files: &[PackageFile]) -> String {
    let mut files = files.to_vec();
    files.sort_by(|left, right| left.path.cmp(right.path));
    let mut hasher = Sha256::new();
    for file in files {
        hasher.update(file.path.as_bytes());
        hasher.update([0]);
        hasher.update((file.mode & 0o777).to_be_bytes());
        hasher.update(file.contents.as_bytes());
        hasher.update([0]);
    }
    format!("{:x}", hasher.finalize())
}

fn stage_lsp_package(lease: &eos_e2e_test::NodeLease<'_>, digest: &str) -> Result<String> {
    let staged = format!("/eos/scratch/uploads/plugins/lsp/{digest}/upload-1/package");
    let mut cmd = format!(
        "set -eu\npkg={}\nrm -rf \"$pkg\"\nmkdir -p \"$pkg/runtime\"\nprintf %s {} > \"$pkg/.package-sha256\"\n",
        shell_quote(&staged),
        shell_quote(digest)
    );
    for file in PACKAGE_FILES {
        cmd.push_str(&format!(
            "printf %s {} > \"$pkg/{}\"\n",
            shell_quote(file.contents),
            file.path
        ));
        if file.mode & 0o111 != 0 {
            cmd.push_str(&format!("chmod +x \"$pkg/{}\"\n", file.path));
        }
    }
    let response = lease.call_ok(ops::API_V1_EXEC_COMMAND, json!({"cmd": cmd}))?;
    if response.get("status").and_then(Value::as_str) == Some("error") {
        anyhow::bail!("LSP package staging command failed: {response}");
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

fn assert_container_dir(lease: &eos_e2e_test::NodeLease<'_>, path: &str) -> Result<()> {
    let response = lease.call_ok(
        ops::API_V1_EXEC_COMMAND,
        json!({"cmd": format!("test -d {}", shell_quote(path))}),
    )?;
    if response.get("status").and_then(Value::as_str) == Some("error") {
        anyhow::bail!("expected container dir {path}: {response}");
    }
    Ok(())
}

fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\"'\"'"))
}
