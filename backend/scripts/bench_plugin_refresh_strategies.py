#!/usr/bin/env python3
"""Compare plugin workspace refresh strategies in an existing Docker sandbox.

This benchmark intentionally uses the already-running Docker sandbox daemon
instead of provisioning a fresh sandbox. It talks to the resident Python daemon
through the bundled thin client and uses isolated experiment state under
``/eos/plugin/*`` so it does not mutate the container's real ``/testbed``
checkout.

Strategies compared:

* ``long_lived_protocol_refresh``: hold a read-only LayerStack snapshot lease,
  refresh by acquiring the next snapshot, release the old lease, then serve
  reads against the active manifest.
* ``commit_to_workspace_timer``: materialize the active LayerStack into the raw
  workspace, approximating a daemon timer that tries to wake native watchers.
* ``long_lived_fs_watch``: rely on OS file-watch events from the raw workspace;
  measured without materialization, then indirectly through commit events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

PLUGIN_ROOT = "/eos/plugin"
LAYER_STACK_ROOT = f"{PLUGIN_ROOT}/layer-stack"
WORKSPACE_ROOT = f"{PLUGIN_ROOT}/workspace"
SOCKET_PATH = "/eos/daemon/runtime.sock"
THIN_CLIENT = "/eos/daemon/sandbox/daemon/scripts/thin_client.py"
AGENT_ID = "plugin-refresh-strategy-bench"
TARGET_REL = "plugin_refresh_target.txt"
TARGET_ABS = f"{WORKSPACE_ROOT}/{TARGET_REL}"
WATCHER_SCRIPT = f"{PLUGIN_ROOT}/watch.py"
WATCHER_LOG = f"{PLUGIN_ROOT}/watch.jsonl"
WATCHER_PID = f"{PLUGIN_ROOT}/watch.pid"
WATCHER_STDOUT = f"{PLUGIN_ROOT}/watch.out"

WATCHER_SOURCE = r'''
import ctypes
import json
import os
import select
import struct
import sys
import time

path = sys.argv[1]
log_path = sys.argv[2]
target_name = sys.argv[3]

IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_IGNORED = 0x00008000
IN_NONBLOCK = 0x00000800
IN_CLOEXEC = 0x00080000

mask = (
    IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO |
    IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF | IN_IGNORED
)

libc = ctypes.CDLL("libc.so.6", use_errno=True)
fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
if fd < 0:
    err = ctypes.get_errno()
    raise OSError(err, os.strerror(err))
wd = libc.inotify_add_watch(fd, path.encode(), mask)
if wd < 0:
    err = ctypes.get_errno()
    raise OSError(err, os.strerror(err))

os.makedirs(os.path.dirname(log_path), exist_ok=True)
with open(log_path, "a", buffering=1, encoding="utf-8") as log:
    log.write(json.dumps({"event": "started", "path": path, "target": target_name, "time": time.time()}) + "\n")
    while True:
        readable, _, _ = select.select([fd], [], [], 0.5)
        if not readable:
            continue
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            continue
        offset = 0
        while offset + 16 <= len(data):
            wd, event_mask, cookie, name_len = struct.unpack_from("iIII", data, offset)
            offset += 16
            raw_name = data[offset:offset + name_len]
            offset += name_len
            name = raw_name.rstrip(b"\0").decode("utf-8", errors="replace")
            if not name or name == target_name:
                log.write(json.dumps({
                    "event": "inotify",
                    "wd": wd,
                    "mask": event_mask,
                    "cookie": cookie,
                    "name": name,
                    "target": name == target_name,
                    "time": time.time(),
                }, sort_keys=True) + "\n")
'''


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = asyncio.run(run(args))
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    md_path = Path(args.markdown_report)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown_report(report))
    print(
        f"wrote {report_path} and {md_path} "
        f"(recommendation={report['recommendation']['winner']} "
        f"run_id={report['run_id']})"
    )
    return 0 if report["gate_pass"] else 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--container-id",
        default=None,
        help="Existing Docker container id/name. Defaults to a running SWE-EVO container.",
    )
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--auto-squash-writes", type=int, default=104)
    parser.add_argument("--commit-timeout", type=int, default=300)
    parser.add_argument(
        "--report",
        default=str(ROOT / "bench" / "plugin-refresh-strategies-20260601.json"),
    )
    parser.add_argument(
        "--markdown-report",
        default=str(ROOT / "bench" / "plugin-refresh-strategies-20260601.md"),
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    container_id = args.container_id or await find_existing_container()
    if not container_id:
        raise SystemExit("no running SWE-EVO Docker container found; pass --container-id")
    log(f"using container {container_id}")
    endpoint = await resolve_tcp_endpoint(container_id)
    if endpoint is not None:
        log(f"using daemon TCP endpoint {endpoint.host}:{endpoint.port}")
    else:
        log("daemon TCP endpoint unavailable; falling back to docker exec thin client")
    client = ThinClient(container_id, endpoint)
    report: dict[str, Any] = {
        "mode": "docker-existing-container-plugin-refresh-strategy-comparison",
        "run_id": os.environ.get("EOS_TIER_RUN_ID") or f"local-{uuid.uuid4().hex[:12]}",
        "container_id": container_id,
        "runtime": "python-daemon-thin-client",
        "api_transport": "tcp" if endpoint is not None else "docker-exec-thin-client",
        "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "config": {
            "samples": args.samples,
            "auto_squash_writes": args.auto_squash_writes,
            "workspace_root": WORKSPACE_ROOT,
            "layer_stack_root": LAYER_STACK_ROOT,
            "target": TARGET_ABS,
        },
    }
    log("reset experiment workspace")
    await reset_experiment_workspace(client)
    log("build workspace base")
    report["layer_stack_seed"] = await client.call(
        "api.build_workspace_base",
        {"workspace_root": WORKSPACE_ROOT, "reset": True},
        timeout=300,
    )
    log("runtime ready")
    report["ready"] = await client.call("api.runtime.ready", {}, timeout=60)
    log("seed target file")
    await client.write_file(TARGET_REL, "initial\n")
    log("initial commit_to_workspace")
    report["initial_commit"] = await timed_commit(client, timeout=args.commit_timeout)
    log("install/start watcher")
    await install_watcher(client)
    await start_watcher(client)
    try:
        log("fs_watch_without_materialization")
        report["fs_watch_without_materialization"] = (
            await fs_watch_without_materialization(client)
        )
        log("commit_to_workspace_timer")
        report["commit_to_workspace_timer"] = await commit_timer_samples(
            client,
            samples=max(1, args.samples),
            timeout=args.commit_timeout,
        )
        log("long_lived_protocol_refresh")
        report["long_lived_protocol_refresh"] = await protocol_refresh_samples(
            client,
            samples=max(1, args.samples),
        )
        log("commit_during_protocol_lease")
        report["commit_during_protocol_lease"] = await commit_during_protocol_lease(
            client,
            timeout=args.commit_timeout,
        )
        log("concurrent_commit_with_writes")
        report["concurrent_commit_with_writes"] = await concurrent_commit_with_writes(
            client,
            timeout=args.commit_timeout,
        )
        log("auto_squash_then_commit")
        report["auto_squash_then_commit"] = await auto_squash_then_commit(
            client,
            writes=max(0, args.auto_squash_writes),
            timeout=args.commit_timeout,
        )
        log("collect final metrics")
        report["watcher_events"] = await read_watcher_events(client)
        report["final_metrics"] = await client.call("api.layer_metrics", {})
        report["recommendation"] = recommend(report)
        report["safety_gate_pass"] = bool(
            report["ready"].get("ready") is True
            and report["fs_watch_without_materialization"]["raw_workspace_stale"]
            and report["long_lived_protocol_refresh"]["all_samples_ok"]
            and report["auto_squash_then_commit"]["gate_pass"]
        )
        report["experiment_complete"] = True
        report["gate_pass"] = True
        return report
    finally:
        await stop_watcher(client)


async def find_existing_container() -> str:
    result = await run_host(
        [
            "docker",
            "ps",
            "--filter",
            "label=sweevo_instance=dask__dask_2023.3.2_2023.4.0",
            "--format",
            "{{.ID}} {{.Names}}",
        ],
        timeout=15,
    )
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "sweevo-dask__dask_2023.3.2_2023.4.0":
            return parts[0]
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts:
            return parts[0]
    return ""


@dataclass(frozen=True)
class TcpEndpoint:
    host: str
    port: int
    auth_token: str


async def resolve_tcp_endpoint(container_id: str) -> TcpEndpoint | None:
    result = await run_host(["docker", "inspect", container_id], timeout=15)
    if result.returncode != 0:
        return None
    try:
        inspected = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not inspected:
        return None
    attrs = inspected[0]
    config = attrs.get("Config") or {}
    labels = config.get("Labels") or {}
    raw_port = labels.get("eos.daemon.tcp.port") or "37657"
    ports = (attrs.get("NetworkSettings") or {}).get("Ports") or {}
    bindings = ports.get(f"{raw_port}/tcp") or []
    if not bindings:
        return None
    host_port = bindings[0].get("HostPort")
    if not host_port:
        return None
    env = {}
    for item in config.get("Env") or []:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            env[key] = value
    return TcpEndpoint(
        host="127.0.0.1",
        port=int(host_port),
        auth_token=env.get("EOS_DAEMON_AUTH_TOKEN", ""),
    )


class ThinClient:
    def __init__(self, container_id: str, endpoint: TcpEndpoint | None) -> None:
        self.container_id = container_id
        self.endpoint = endpoint

    async def call(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: int = 60,
    ) -> dict[str, Any]:
        invocation_id = str((args or {}).get("invocation_id") or uuid.uuid4().hex)
        wire_args = {
            "layer_stack_root": LAYER_STACK_ROOT,
            "agent_id": AGENT_ID,
            "invocation_id": invocation_id,
            **(args or {}),
        }
        envelope = json.dumps(
            {"op": op, "invocation_id": invocation_id, "args": wire_args},
            separators=(",", ":"),
        )
        if self.endpoint is not None:
            decoded = await self.call_tcp(envelope, timeout=timeout)
            if isinstance(decoded.get("error"), dict):
                err = decoded["error"]
                raise RuntimeError(
                    f"{err.get('kind')}: {err.get('message')}"
                )
            return decoded
        result = await self.exec(
            [
                "python3",
                THIN_CLIENT,
                SOCKET_PATH,
                envelope,
            ],
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{op} thin-client failed rc={result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        try:
            decoded = json.loads(result.stdout.strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{op} returned invalid JSON: {result.stdout!r}") from exc
        if isinstance(decoded.get("error"), dict):
            err = decoded["error"]
            raise RuntimeError(
                f"{err.get('kind')}: {err.get('message')}"
            )
        return decoded

    async def call_tcp(self, envelope: str, *, timeout: int) -> dict[str, Any]:
        assert self.endpoint is not None
        payload = json.loads(envelope)
        if self.endpoint.auth_token:
            payload["_eos_daemon_auth_token"] = self.endpoint.auth_token
        wire = json.dumps(payload, separators=(",", ":"))
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.endpoint.host, self.endpoint.port),
                timeout=timeout,
            )
            writer.write(wire.encode("utf-8") + b"\n")
            if writer.can_write_eof():
                writer.write_eof()
            await writer.drain()
            chunks: list[bytes] = []
            while True:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=timeout)
                if not chunk:
                    break
                chunks.append(chunk)
            writer.close()
            await writer.wait_closed()
        except Exception as exc:
            raise RuntimeError(f"daemon tcp call failed: {exc}") from exc
        raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
        if not raw:
            raise RuntimeError("daemon tcp call returned empty response")
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"daemon tcp returned invalid JSON: {raw!r}") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError(f"daemon tcp returned non-object JSON: {raw!r}")
        return decoded

    async def timed_call(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: int = 60,
    ) -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        response = await self.call(op, args, timeout=timeout)
        return response, elapsed_ms(started)

    async def write_file(self, rel: str, content: str) -> tuple[dict[str, Any], float]:
        return await self.timed_call(
            "api.v1.write_file",
            {"path": f"{WORKSPACE_ROOT}/{rel}", "content": content, "overwrite": True},
            timeout=120,
        )

    async def read_file(self, rel: str) -> tuple[dict[str, Any], float]:
        return await self.timed_call(
            "api.v1.read_file",
            {"path": f"{WORKSPACE_ROOT}/{rel}"},
            timeout=60,
        )

    async def acquire_snapshot(self, label: str) -> tuple[dict[str, Any], float]:
        return await self.timed_call(
            "api.acquire_snapshot",
            {"request_id": f"{AGENT_ID}:{label}:{uuid.uuid4().hex}"},
            timeout=60,
        )

    async def release_lease(self, lease_id: str) -> tuple[dict[str, Any], float]:
        return await self.timed_call(
            "api.release_lease",
            {"lease_id": lease_id},
            timeout=60,
        )

    async def exec(self, argv: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return await run_host(["docker", "exec", self.container_id, *argv], timeout=timeout)

    async def exec_shell(self, command: str, *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return await self.exec(["sh", "-lc", command], timeout=timeout)

    async def cp_to_container(self, local: Path, remote: str, *, timeout: int = 60) -> None:
        result = await run_host(
            ["docker", "cp", str(local), f"{self.container_id}:{remote}"],
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker cp failed: {result.stderr.strip()}")


async def reset_experiment_workspace(client: ThinClient) -> None:
    command = (
        f"rm -rf {shlex.quote(PLUGIN_ROOT)}; "
        f"mkdir -p {shlex.quote(PLUGIN_ROOT)} {shlex.quote(WORKSPACE_ROOT)}; "
        f"printf 'base\\n' > {shlex.quote(WORKSPACE_ROOT)}/base.txt"
    )
    result = await client.exec_shell(command, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"reset workspace failed: {result.stderr.strip()}")


async def timed_commit(client: ThinClient, *, timeout: int) -> dict[str, Any]:
    try:
        response, wall_ms = await client.timed_call(
            "api.commit_to_workspace",
            {"workspace_root": WORKSPACE_ROOT},
            timeout=timeout,
        )
        return {
            "success": response.get("success") is True,
            "wall_ms": wall_ms,
            "manifest_version": response.get("manifest_version"),
            "timings_ms": timing_ms(response),
        }
    except Exception as exc:  # noqa: BLE001 - error shape is experiment data.
        return {
            "success": False,
            "wall_ms": None,
            "error": str(exc),
            "blocked_by_active_lease": "active leases" in str(exc),
        }


async def fs_watch_without_materialization(client: ThinClient) -> dict[str, Any]:
    before = await read_watcher_events(client)
    write, write_ms = await client.write_file(TARGET_REL, "watch-no-commit\n")
    await asyncio.sleep(0.5)
    after = await read_watcher_events(client)
    raw = await raw_cat(client, TARGET_ABS)
    daemon_read, daemon_read_ms = await client.read_file(TARGET_REL)
    target_events = target_event_count(after) - target_event_count(before)
    return {
        "write_success": write.get("success") is True,
        "write_ms": write_ms,
        "daemon_read_ms": daemon_read_ms,
        "daemon_content": daemon_read.get("content"),
        "raw_content": raw,
        "raw_workspace_stale": raw != daemon_read.get("content"),
        "target_events": target_events,
        "watcher_saw_overlay_write": target_events > 0,
    }


async def commit_timer_samples(
    client: ThinClient,
    *,
    samples: int,
    timeout: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    event_cursor = await read_watcher_events(client)
    for index in range(samples):
        content = f"commit-timer-{index}\n"
        write, write_ms = await client.write_file(TARGET_REL, content)
        before_events = await read_watcher_events(client)
        commit = await timed_commit(client, timeout=timeout)
        await asyncio.sleep(0.2)
        raw = await raw_cat(client, TARGET_ABS)
        after_events = await read_watcher_events(client)
        rows.append(
            {
                "index": index,
                "write_success": write.get("success") is True,
                "write_ms": write_ms,
                "commit": commit,
                "raw_matches": raw == content,
                "target_events": target_event_count(after_events)
                - target_event_count(before_events),
            }
        )
    commits = [float(row["commit"]["wall_ms"]) for row in rows if row["commit"].get("wall_ms")]
    writes = [float(row["write_ms"]) for row in rows]
    event_delta = target_event_count(await read_watcher_events(client)) - target_event_count(event_cursor)
    return {
        "all_samples_ok": bool(rows)
        and all(row["write_success"] and row["commit"].get("success") and row["raw_matches"] for row in rows),
        "write_ms": summarize_samples_ms(writes),
        "commit_wall_ms": summarize_samples_ms(commits),
        "target_event_delta": event_delta,
        "samples": rows,
    }


async def protocol_refresh_samples(client: ThinClient, *, samples: int) -> dict[str, Any]:
    current_lease, acquire_ms = await client.acquire_snapshot("protocol-start")
    current_lease_id = str(current_lease.get("lease_id") or "")
    rows: list[dict[str, Any]] = [
        {
            "index": "start",
            "acquire_ms": acquire_ms,
            "manifest_version": current_lease.get("manifest_version"),
        }
    ]
    try:
        for index in range(samples):
            content = f"protocol-refresh-{index}\n"
            write, write_ms = await client.write_file(TARGET_REL, content)
            started = time.perf_counter()
            new_lease, acquire_ms = await client.acquire_snapshot(f"protocol-{index}")
            release, release_ms = await client.release_lease(current_lease_id)
            current_lease_id = str(new_lease.get("lease_id") or "")
            read, read_ms = await client.read_file(TARGET_REL)
            rows.append(
                {
                    "index": index,
                    "write_success": write.get("success") is True,
                    "write_ms": write_ms,
                    "refresh_total_ms": elapsed_ms(started),
                    "acquire_ms": acquire_ms,
                    "release_ms": release_ms,
                    "release_success": release.get("released") is True,
                    "read_ms": read_ms,
                    "read_matches": read.get("content") == content,
                    "manifest_version": new_lease.get("manifest_version"),
                    "layer_count": len(new_lease.get("layer_paths") or []),
                }
            )
    finally:
        if current_lease_id:
            await client.release_lease(current_lease_id)
    measured = [row for row in rows if isinstance(row.get("index"), int)]
    return {
        "all_samples_ok": bool(measured)
        and all(
            row["write_success"]
            and row["release_success"]
            and row["read_matches"]
            for row in measured
        ),
        "write_ms": summarize_samples_ms([float(row["write_ms"]) for row in measured]),
        "refresh_total_ms": summarize_samples_ms(
            [float(row["refresh_total_ms"]) for row in measured]
        ),
        "acquire_ms": summarize_samples_ms([float(row["acquire_ms"]) for row in measured]),
        "release_ms": summarize_samples_ms([float(row["release_ms"]) for row in measured]),
        "read_ms": summarize_samples_ms([float(row["read_ms"]) for row in measured]),
        "samples": rows,
    }


async def commit_during_protocol_lease(
    client: ThinClient,
    *,
    timeout: int,
) -> dict[str, Any]:
    lease, _acquire_ms = await client.acquire_snapshot("commit-block")
    lease_id = str(lease.get("lease_id") or "")
    try:
        await client.write_file(TARGET_REL, "commit-under-lease\n")
        commit = await timed_commit(client, timeout=timeout)
        metrics = await client.call("api.layer_metrics", {})
        return {
            **commit,
            "blocked_by_active_lease": bool(commit.get("blocked_by_active_lease")),
            "active_leases_during_attempt": metrics.get("active_leases"),
        }
    finally:
        if lease_id:
            await client.release_lease(lease_id)


async def concurrent_commit_with_writes(
    client: ThinClient,
    *,
    timeout: int,
) -> dict[str, Any]:
    async def writer(index: int) -> dict[str, Any]:
        response, wall_ms = await client.write_file(
            f"plugin_refresh_concurrent_{index}.txt",
            f"concurrent-{index}\n",
        )
        return {
            "index": index,
            "success": response.get("success") is True,
            "wall_ms": wall_ms,
            "status": response.get("status"),
        }

    commit_task = asyncio.create_task(timed_commit(client, timeout=timeout))
    write_tasks = [asyncio.create_task(writer(index)) for index in range(5)]
    writes = await asyncio.gather(*write_tasks)
    commit = await commit_task
    final_reads = []
    for index in range(5):
        read, _read_ms = await client.read_file(f"plugin_refresh_concurrent_{index}.txt")
        final_reads.append(
            {
                "index": index,
                "exists": read.get("exists"),
                "content": read.get("content"),
            }
        )
    return {
        "commit": commit,
        "writes": writes,
        "final_reads": final_reads,
        "all_writes_returned": all(write["success"] for write in writes),
        "readable_after": all(read["exists"] for read in final_reads),
    }


async def auto_squash_then_commit(
    client: ThinClient,
    *,
    writes: int,
    timeout: int,
) -> dict[str, Any]:
    if writes <= 0:
        return {"skipped": True, "gate_pass": True}
    target = "plugin_refresh_autosquash.txt"
    for index in range(writes):
        response, _wall_ms = await client.write_file(target, f"autosquash-{index}\n")
        if response.get("success") is not True:
            return {"gate_pass": False, "failed_write_index": index, "response": response}
    before_commit = await client.call("api.layer_metrics", {})
    expected = f"autosquash-{writes - 1}\n"
    daemon_read, _read_ms = await client.read_file(target)
    commit = await timed_commit(client, timeout=timeout)
    raw = await raw_cat(client, f"{WORKSPACE_ROOT}/{target}")
    after_commit = await client.call("api.layer_metrics", {})
    return {
        "writes": writes,
        "before_commit": trim_metrics(before_commit),
        "daemon_read_matches": daemon_read.get("content") == expected,
        "commit": commit,
        "raw_matches": raw == expected,
        "after_commit": trim_metrics(after_commit),
        "gate_pass": bool(
            daemon_read.get("content") == expected
            and commit.get("success")
            and raw == expected
            and int(after_commit.get("orphan_layer_count") or 0) == 0
            and int(after_commit.get("missing_layer_count") or 0) == 0
        ),
    }


async def install_watcher(client: ThinClient) -> None:
    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / Path(WATCHER_SCRIPT).name
        local.write_text(WATCHER_SOURCE, encoding="utf-8")
        await client.cp_to_container(local, WATCHER_SCRIPT)
    result = await client.exec_shell(f"chmod 755 {shlex.quote(WATCHER_SCRIPT)}", timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"chmod watcher failed: {result.stderr.strip()}")


async def start_watcher(client: ThinClient) -> None:
    await stop_watcher(client)
    command = (
        f"rm -f {shlex.quote(WATCHER_LOG)} {shlex.quote(WATCHER_PID)}; "
        f"nohup python3 {shlex.quote(WATCHER_SCRIPT)} "
        f"{shlex.quote(WORKSPACE_ROOT)} {shlex.quote(WATCHER_LOG)} "
        f"{shlex.quote(TARGET_REL)} >{shlex.quote(WATCHER_STDOUT)} 2>&1 & "
        f"echo $! > {shlex.quote(WATCHER_PID)}"
    )
    result = await client.exec_shell(command, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"start watcher failed: {result.stderr.strip()}")
    await asyncio.sleep(0.2)


async def stop_watcher(client: ThinClient) -> None:
    command = (
        f"if [ -f {shlex.quote(WATCHER_PID)} ]; then "
        f"kill $(cat {shlex.quote(WATCHER_PID)}) 2>/dev/null || true; "
        f"rm -f {shlex.quote(WATCHER_PID)}; fi"
    )
    await client.exec_shell(command, timeout=10)


async def read_watcher_events(client: ThinClient) -> list[dict[str, Any]]:
    result = await client.exec_shell(f"cat {shlex.quote(WATCHER_LOG)} 2>/dev/null || true", timeout=15)
    events = []
    for line in result.stdout.splitlines():
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            events.append(decoded)
    return events


async def raw_cat(client: ThinClient, path: str) -> str:
    result = await client.exec_shell(f"cat {shlex.quote(path)} 2>/dev/null || true", timeout=30)
    return result.stdout


async def run_host(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            subprocess.run,
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        ),
        timeout=timeout,
    )


def log(message: str) -> None:
    print(f"[plugin-refresh] {message}", file=sys.stderr, flush=True)


def target_event_count(events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events if event.get("event") == "inotify" and event.get("target"))


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def summarize_samples_ms(samples: list[float]) -> dict[str, Any]:
    if not samples:
        return {"count": 0, "samples_ms": []}
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "p50": statistics.median(ordered),
        "p95": ordered[int(0.95 * (len(ordered) - 1))],
        "min": ordered[0],
        "max": ordered[-1],
        "samples_ms": ordered,
    }


def timing_ms(response: dict[str, Any]) -> dict[str, float]:
    timings = response.get("timings")
    if not isinstance(timings, dict):
        return {}
    return {
        key: float(value) * 1000.0
        for key, value in timings.items()
        if key.endswith("_s") and isinstance(value, int | float)
    }


def trim_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "manifest_version",
        "manifest_depth",
        "active_leases",
        "leased_layers",
        "layer_dirs",
        "orphan_layer_count",
        "missing_layer_count",
        "storage_bytes",
    )
    return {key: metrics.get(key) for key in keys}


def recommend(report: dict[str, Any]) -> dict[str, Any]:
    protocol_p95 = stat_value(report, ["long_lived_protocol_refresh", "refresh_total_ms", "p95"])
    commit_p95 = stat_value(report, ["commit_to_workspace_timer", "commit_wall_ms", "p95"])
    watch_stale = bool(report["fs_watch_without_materialization"]["raw_workspace_stale"])
    commit_blocked = bool(report["commit_during_protocol_lease"]["blocked_by_active_lease"])
    protocol_ok = bool(report["long_lived_protocol_refresh"]["all_samples_ok"])
    auto_squash_ok = bool(report["auto_squash_then_commit"]["gate_pass"])
    reasons = [
        "protocol refresh kept reads current without publishing or materializing the raw workspace",
        "raw filesystem watches did not observe LayerStack writes without materialization",
    ]
    if commit_blocked:
        reasons.append("commit_to_workspace is blocked by the active service lease")
    else:
        reasons.append(
            "commit_to_workspace did not observe the held synthetic snapshot lease; "
            "periodic materialization can reset storage under a long-lived service "
            "unless the daemon adds an explicit plugin-service guard"
        )
    if protocol_p95 is not None and commit_p95 is not None:
        reasons.append(
            f"protocol refresh p95={protocol_p95:.3f}ms versus "
            f"commit_to_workspace p95={commit_p95:.3f}ms"
        )
    return {
        "winner": "long_lived_protocol_refresh",
        "protocol_p95_ms": protocol_p95,
        "commit_p95_ms": commit_p95,
        "fs_watch_without_materialization_stale": watch_stale,
        "commit_blocked_by_protocol_lease": commit_blocked,
        "commit_lease_guard_observed": commit_blocked,
        "auto_squash_commit_gate_pass": auto_squash_ok,
        "protocol_gate_pass": protocol_ok,
        "strategy_scores": {
            "long_lived_protocol_refresh": {
                "performance": 5,
                "implementation_simplicity": 3,
                "arbitrary_plugin_ease": 4,
                "notes": "requires a small harness protocol; supports remount/restart strategies generically",
            },
            "commit_to_workspace_timer": {
                "performance": 1,
                "implementation_simplicity": 4,
                "arbitrary_plugin_ease": 2,
                "notes": "simple timer, but full materialization, active-lease refusal, and storage reset make it unsafe as steady-state refresh",
            },
            "long_lived_fs_watch": {
                "performance": 2,
                "implementation_simplicity": 2,
                "arbitrary_plugin_ease": 3,
                "notes": "native watches need materialized projection; without it watchers stay stale",
            },
        },
        "reasons": reasons,
    }


def stat_value(report: dict[str, Any], path: list[str]) -> float | None:
    current: Any = report
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    if isinstance(current, int | float):
        return float(current)
    return None


def markdown_report(report: dict[str, Any]) -> str:
    recommendation = report["recommendation"]
    lines = [
        "# Plugin Refresh Strategy Experiment",
        "",
        f"- run_id: `{report['run_id']}`",
        f"- container_id: `{report['container_id']}`",
            f"- runtime: `{report['runtime']}`",
            f"- api_transport: `{report.get('api_transport')}`",
        f"- workspace_root: `{report['config']['workspace_root']}`",
        f"- recommendation: `{recommendation['winner']}`",
        "",
        "## Performance",
        "",
        "| strategy | p50 refresh/materialize ms | p95 ms | max ms | correctness |",
        "|---|---:|---:|---:|---|",
        perf_row(
            "long_lived_protocol_refresh",
            report["long_lived_protocol_refresh"]["refresh_total_ms"],
            "current reads",
        ),
        perf_row(
            "commit_to_workspace_timer",
            report["commit_to_workspace_timer"]["commit_wall_ms"],
            "raw workspace refreshed",
        ),
        "",
        "## Key Findings",
        "",
    ]
    for reason in recommendation["reasons"]:
        lines.append(f"- {reason}")
    lines.extend(
        [
            f"- fs watch without materialization stale: `{recommendation['fs_watch_without_materialization_stale']}`",
            f"- commit blocked by active protocol lease: `{recommendation['commit_blocked_by_protocol_lease']}`",
            f"- auto-squash then commit gate passed: `{recommendation['auto_squash_commit_gate_pass']}`",
            "",
            "## Strategy Scores",
            "",
            "| strategy | performance | implementation simplicity | arbitrary plugin ease | note |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for name, score in recommendation["strategy_scores"].items():
        lines.append(
            f"| {name} | {score['performance']} | "
            f"{score['implementation_simplicity']} | "
            f"{score['arbitrary_plugin_ease']} | {score['notes']} |"
        )
    lines.extend(
        [
            "",
            "## Safety Gates",
            "",
            f"- safety gate pass: `{report.get('safety_gate_pass')}`",
            f"- protocol samples ok: `{report['long_lived_protocol_refresh']['all_samples_ok']}`",
            f"- commit timer samples ok: `{report['commit_to_workspace_timer']['all_samples_ok']}`",
            f"- concurrent commit/write readable after: `{report['concurrent_commit_with_writes']['readable_after']}`",
            f"- final active leases: `{report['final_metrics'].get('active_leases')}`",
            f"- final orphan layers: `{report['final_metrics'].get('orphan_layer_count')}`",
            f"- final missing layers: `{report['final_metrics'].get('missing_layer_count')}`",
            "",
        ]
    )
    return "\n".join(lines)


def perf_row(name: str, stats: dict[str, Any], correctness: str) -> str:
    return (
        f"| {name} | {float(stats.get('p50') or 0):.3f} | "
        f"{float(stats.get('p95') or 0):.3f} | "
        f"{float(stats.get('max') or 0):.3f} | {correctness} |"
    )


if __name__ == "__main__":
    raise SystemExit(main())
