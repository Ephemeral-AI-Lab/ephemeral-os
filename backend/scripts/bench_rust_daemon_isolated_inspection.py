#!/usr/bin/env python3
"""Live Docker proof for Rust isolated-workspace exit inspection."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shlex
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = ROOT / "backend" / "src"
SCRIPT_DIR = Path(__file__).resolve().parent
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bench_rust_daemon_phase2 import (  # noqa: E402
    LAYER_STACK_ROOT,
    WORKSPACE_ROOT,
    require_success,
    reset_runtime,
    temporary_env,
    upload_artifact,
)
from bench_sandbox_e2e import (  # noqa: E402
    DEFAULT_DOCKER_IMAGE,
    DockerBench,
    _exit_code,
    _text,
    elapsed_ms,
)

AGENT_ID = "phase3t-isolated-inspection"
AUDIT_PATH = "/tmp/eos-iws-inspection-audit.jsonl"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    with optional_env("EOS_DOCKER_PRIVILEGED", "1" if args.privileged else None):
        report = asyncio.run(run(args))
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(
        f"wrote {out} "
        f"(gate={report['gate_pass']} run_id={report['run_id']} "
        f"artifact={report['artifact']['local_sha256']})"
    )
    return 0 if report["gate_pass"] else 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument("--container-id", default=None)
    parser.add_argument(
        "--artifact",
        type=Path,
        default=ROOT / "sandbox" / "dist" / "eosd-linux-amd64",
    )
    parser.add_argument(
        "--report",
        default=str(ROOT / "bench" / "phase3t-rust-isolated-inspection-docker-20260601.json"),
    )
    parser.add_argument("--keep-container", action="store_true")
    parser.add_argument("--name-prefix", default="eos-phase3t-iws")
    parser.add_argument(
        "--privileged",
        action="store_true",
        help="Create a privileged Docker container instead of the default capability set.",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.artifact.exists():
        raise SystemExit(f"missing eosd artifact: {args.artifact}")
    bench = await DockerBench.create(
        image=args.docker_image,
        container_id=args.container_id,
        name_prefix=args.name_prefix,
    )
    try:
        report: dict[str, Any] = {
            "mode": "docker-phase3t-isolated-inspection",
            "run_id": os.environ.get("EOS_TIER_RUN_ID") or f"local-{uuid.uuid4().hex[:12]}",
            "sandbox_id": bench.sandbox_id,
            "created_container": bench.created,
            "host": {
                "platform": platform.platform(),
                "python": sys.version.split()[0],
            },
            "agent_id": AGENT_ID,
            "audit_path": AUDIT_PATH,
        }
        await reset_runtime(bench)
        await configure_container_environment(bench)
        report["preflight"] = await collect_preflight(bench)
        report["artifact"] = await upload_artifact(bench, args.artifact)

        with temporary_env("EOS_SANDBOX_RUNTIME", "rust"):
            from sandbox.host import daemon_client

            daemon_client.invalidate_daemon_tcp_endpoint(bench.sandbox_id)
            started = time.perf_counter()
            await daemon_client.ensure_daemon_current(bench.sandbox_id)
            report["daemon_spawn_ms"] = elapsed_ms(started)
            endpoint = await daemon_client._resolve_daemon_tcp_endpoint(  # noqa: SLF001
                bench.adapter,
                bench.sandbox_id,
            )
            if endpoint is None:
                raise RuntimeError("Docker sandbox did not expose a daemon TCP endpoint")
            report["endpoint"] = {
                "host": endpoint.host,
                "port": endpoint.port,
                "internal_port": endpoint.internal_port,
                "auth_token_present": bool(endpoint.auth_token),
            }
            report["layer_stack_seed"] = await daemon_client.call_daemon_api(
                bench.sandbox_id,
                "api.build_workspace_base",
                {"workspace_root": WORKSPACE_ROOT, "reset": True},
                layer_stack_root=LAYER_STACK_ROOT,
                timeout=180,
            )
            report["ready"] = await daemon_client.call_daemon_api(
                bench.sandbox_id,
                "api.runtime.ready",
                {},
                layer_stack_root=LAYER_STACK_ROOT,
                timeout=30,
            )
            client = IsolatedClient(daemon_client, endpoint)
            report["scenario"] = await run_scenario(bench, client)

        report["gate_pass"] = bool(
            report["artifact"]["gate_pass"]
            and report["ready"].get("ready") is True
            and report["scenario"]["gate_pass"]
        )
        return report
    finally:
        await bench.close(keep=args.keep_container)


class IsolatedClient:
    def __init__(self, daemon_client: Any, endpoint: Any) -> None:
        self.daemon_client = daemon_client
        self.endpoint = endpoint

    async def call(self, op: str, args: dict[str, Any] | None = None) -> tuple[dict[str, Any], float]:
        payload_args = {
            "layer_stack_root": LAYER_STACK_ROOT,
            "agent_id": AGENT_ID,
            **(args or {}),
        }
        payload = json.dumps(
            {
                "op": op,
                "invocation_id": payload_args.setdefault(
                    "invocation_id", f"phase3t-iws-{uuid.uuid4().hex}"
                ),
                "args": payload_args,
            },
            separators=(",", ":"),
        )
        started = time.perf_counter()
        result = await self.daemon_client._call_tcp_daemon(  # noqa: SLF001
            self.endpoint,
            payload,
            timeout=30,
        )
        require_success(result, f"TCP daemon client {op}")
        return decode_response(result), elapsed_ms(started)

    async def enter(self) -> tuple[dict[str, Any], float]:
        return await self.call("api.isolated_workspace.enter")

    async def status(self) -> tuple[dict[str, Any], float]:
        return await self.call("api.isolated_workspace.status")

    async def list_open(self) -> tuple[dict[str, Any], float]:
        return await self.call("api.isolated_workspace.list_open")

    async def exit(self, *, force_cancel: bool = False, grace_s: float = 2.0) -> tuple[dict[str, Any], float]:
        return await self.call(
            "api.isolated_workspace.exit",
            {"force_cancel": force_cancel, "grace_s": grace_s},
        )

    async def exec_command(
        self,
        cmd: str,
        *,
        tty: bool,
        yield_time_ms: int = 1000,
        timeout: int = 30,
    ) -> tuple[dict[str, Any], float]:
        return await self.call(
            "api.v1.exec_command",
            {
                "cmd": cmd,
                "tty": tty,
                "yield_time_ms": yield_time_ms,
                "timeout": timeout,
            },
        )

    async def read_file(self, path: str) -> tuple[dict[str, Any], float]:
        return await self.call("api.v1.read_file", {"path": path})


async def run_scenario(bench: DockerBench, client: IsolatedClient) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}

    enter, enter_ms = await client.enter()
    checks["enter_success"] = enter.get("success") is True
    checks["handle_id_present"] = isinstance(enter.get("workspace_handle_id"), str)
    details["enter"] = {"wall_ms": enter_ms, "response": enter}

    status_open, _ = await client.status()
    list_open, _ = await client.list_open()
    checks["status_open"] = status_open.get("open") is True
    checks["list_open_contains_agent"] = AGENT_ID in list_open.get("open_agent_ids", [])

    host_net_after_enter = await host_network_state(bench)
    checks["bridge_created"] = host_net_after_enter["bridge_exists"]
    checks["host_veth_created"] = bool(host_net_after_enter["host_veth_names"])
    details["host_network_after_enter"] = host_net_after_enter

    net_probe, _ = await client.exec_command(
        "cat /proc/net/dev; echo __ROUTE__; cat /proc/net/route",
        tty=False,
    )
    net_text = combined_output(net_probe)
    checks["isolated_command_success"] = net_probe.get("status") == "ok"
    checks["isolated_command_marked_isolated"] = net_probe.get("workspace") == "isolated"
    checks["namespace_veth_visible"] = "eos-iws-" in net_text
    checks["namespace_default_route_visible"] = "00000000" in net_text
    details["network_probe"] = trim_response(net_probe)

    private_path = f"phase3t-iws-private-{uuid.uuid4().hex[:8]}.txt"
    finite_write, _ = await client.exec_command(
        f"printf isolated-finite > {shlex.quote(private_path)}",
        tty=False,
    )
    shared_read_during, _ = await client.read_file(f"{WORKSPACE_ROOT}/{private_path}")
    checks["finite_write_success"] = finite_write.get("status") == "ok"
    checks["finite_write_not_published_during_open"] = not read_exists(shared_read_during)

    pty_path = f"phase3t-iws-pty-{uuid.uuid4().hex[:8]}.txt"
    pty_write, _ = await client.exec_command(
        f"printf isolated-pty > {shlex.quote(pty_path)}",
        tty=True,
    )
    pty_visible_inside, _ = await client.exec_command(
        f"test -f {shlex.quote(pty_path)} && cat {shlex.quote(pty_path)}",
        tty=False,
    )
    shared_read_pty, _ = await client.read_file(f"{WORKSPACE_ROOT}/{pty_path}")
    checks["pty_natural_write_success"] = pty_write.get("status") == "ok"
    checks["pty_natural_write_visible_inside"] = "isolated-pty" in combined_output(pty_visible_inside)
    checks["pty_natural_write_not_published"] = not read_exists(shared_read_pty)
    details["private_write"] = {
        "finite": trim_response(finite_write),
        "shared_read_during": trim_response(shared_read_during),
        "pty": trim_response(pty_write),
        "pty_inside": trim_response(pty_visible_inside),
        "shared_read_pty": trim_response(shared_read_pty),
    }

    long_pty, _ = await client.exec_command("sleep 60", tty=True, yield_time_ms=50, timeout=120)
    pty_session_id = str(long_pty.get("pty_session_id") or "")
    checks["long_pty_started"] = long_pty.get("status") == "running" and bool(pty_session_id)
    blocked_exit, _ = await client.exit(force_cancel=False)
    checks["nonforced_exit_blocks_active_pty"] = (
        blocked_exit.get("success") is False
        and nested(blocked_exit, "error", "kind") == "active_pty_sessions"
        and pty_session_id in nested(blocked_exit, "error", "details", "pty_session_ids", default=[])
    )
    force_exit, force_exit_ms = await client.exit(force_cancel=True, grace_s=2.0)
    details["pty_exit"] = {
        "long_pty": trim_response(long_pty),
        "blocked_exit": trim_response(blocked_exit),
        "force_exit_wall_ms": force_exit_ms,
        "force_exit": trim_exit_response(force_exit),
    }

    inspection = force_exit.get("inspection") if isinstance(force_exit.get("inspection"), dict) else {}
    checks.update(inspection_checks(force_exit, inspection, pty_session_id))
    holder_pid = inspection.get("holder_pid")
    holder_process = await process_exists(bench, holder_pid) if isinstance(holder_pid, int) else None
    checks["holder_process_gone"] = holder_process is False

    host_veth = inspection.get("veth_host_name")
    if isinstance(host_veth, str) and host_veth:
        checks["host_veth_removed"] = not await path_exists(bench, f"/sys/class/net/{host_veth}")
    else:
        checks["host_veth_removed"] = False

    status_closed, _ = await client.status()
    list_closed, _ = await client.list_open()
    shared_read_after, _ = await client.read_file(f"{WORKSPACE_ROOT}/{private_path}")
    checks["status_closed"] = status_closed.get("open") is False
    checks["list_open_closed"] = AGENT_ID not in list_closed.get("open_agent_ids", [])
    checks["finite_write_not_published_after_exit"] = not read_exists(shared_read_after)
    details["post_exit"] = {
        "status": status_closed,
        "list_open": list_closed,
        "shared_read_after": trim_response(shared_read_after),
        "holder_process_exists": holder_process,
    }

    audit = await read_audit(bench)
    checks.update(audit_checks(audit, inspection))
    details["audit"] = audit

    return {
        "checks": checks,
        "gate_pass": all(checks.values()),
        "details": details,
    }


def inspection_checks(
    force_exit: dict[str, Any],
    inspection: dict[str, Any],
    pty_session_id: str,
) -> dict[str, bool]:
    cgroup_path = inspection.get("cgroup_path")
    mountinfo_refs = inspection.get("mountinfo_reference_count_after")
    return {
        "force_exit_success": force_exit.get("success") is True,
        "force_cancel_requested": force_exit.get("force_cancel_requested") is True,
        "force_cancelled_real_pty": pty_session_id
        in force_exit.get("force_cancelled_pty_session_ids", []),
        "force_cancel_no_stale_ptys": force_exit.get("stale_pty_session_ids") == [],
        "force_cancel_no_active_ptys_after": force_exit.get("active_pty_session_ids_after") == [],
        "handle_unregistered": inspection.get("handle_registered_after") is False,
        "agent_unregistered": inspection.get("agent_registered_after") is False,
        "open_handle_count_zero": inspection.get("open_handle_count_after") == 0,
        "open_agent_count_zero": inspection.get("open_agent_count_after") == 0,
        "lease_released": inspection.get("lease_released") is True,
        "active_leases_zero": inspection.get("active_leases_after") == 0,
        "holder_pid_real": isinstance(inspection.get("holder_pid"), int)
        and inspection.get("holder_pid", 0) > 0,
        "holder_kill_clean": inspection.get("holder_kill_error") is None,
        "ns_fds_recorded": isinstance(inspection.get("ns_fd_count"), int)
        and inspection.get("ns_fd_count", 0) >= 4,
        "readiness_fd_recorded": inspection.get("readiness_fd_was_open") is True,
        "control_fd_recorded": inspection.get("control_fd_was_open") is True,
        "cgroup_path_recorded": isinstance(cgroup_path, str) and bool(cgroup_path),
        "cgroup_removed": inspection.get("cgroup_exists_after") is False,
        "scratch_removed": inspection.get("scratch_exists_after") is False,
        "upperdir_removed": inspection.get("upperdir_exists_after") is False,
        "workdir_removed": inspection.get("workdir_exists_after") is False,
        "mountinfo_refs_zero": isinstance(mountinfo_refs, int) and mountinfo_refs == 0,
        "veth_names_recorded": isinstance(inspection.get("veth_host_name"), str)
        and isinstance(inspection.get("veth_ns_name"), str),
    }


def audit_checks(audit: dict[str, Any], inspection: dict[str, Any]) -> dict[str, bool]:
    types = [event.get("type") for event in audit.get("events", [])]
    exit_events = [
        event
        for event in audit.get("events", [])
        if event.get("type") == "sandbox_isolated_workspace_exit"
    ]
    enter_events = [
        event
        for event in audit.get("events", [])
        if event.get("type") == "sandbox_isolated_workspace_enter"
    ]
    tool_events = [
        event
        for event in audit.get("events", [])
        if event.get("type") == "sandbox_isolated_workspace_tool_call"
    ]
    exit_inspection = nested(exit_events[-1], "payload", "inspection", default={}) if exit_events else {}
    return {
        "audit_readable": audit.get("readable") is True,
        "audit_has_enter": "sandbox_isolated_workspace_enter" in types,
        "audit_has_tool_call": bool(tool_events),
        "audit_has_exit": "sandbox_isolated_workspace_exit" in types,
        "audit_enter_has_network": bool(enter_events)
        and isinstance(nested(enter_events[-1], "payload", "ns_ip"), str),
        "audit_exit_inspection_matches": isinstance(exit_inspection, dict)
        and exit_inspection.get("active_leases_after") == inspection.get("active_leases_after")
        and exit_inspection.get("scratch_exists_after") == inspection.get("scratch_exists_after")
        and exit_inspection.get("mountinfo_reference_count_after")
        == inspection.get("mountinfo_reference_count_after"),
    }


async def configure_container_environment(bench: DockerBench) -> None:
    assignments = {
        "EOS_ISOLATED_WORKSPACE_ENABLED": "true",
        "EOS_ISOLATED_WORKSPACE_AUDIT_PATH": AUDIT_PATH,
        "EOS_ISOLATED_WORKSPACE_EXIT_GRACE_S": "2.0",
        "EOS_ISOLATED_WORKSPACE_SETUP_TIMEOUT_S": "30.0",
    }
    keys = "|".join(shlex.quote(key) for key in assignments)
    lines = "\n".join(f"{key}={value}" for key, value in assignments.items()) + "\n"
    command = f"""
set -eu
tmp="$(mktemp)"
if [ -f /etc/environment ]; then
  grep -Ev '^({keys})=' /etc/environment > "$tmp" || true
fi
cat >> "$tmp" <<'EOF'
{lines.rstrip()}
EOF
cat "$tmp" > /etc/environment
rm -f "$tmp"
rm -f {shlex.quote(AUDIT_PATH)}
mount -o remount,rw /sys/fs/cgroup 2>/dev/null || true
"""
    require_success(await bench.exec(command, timeout=30), "configure isolated environment")


async def collect_preflight(bench: DockerBench) -> dict[str, Any]:
    tools = await bench.exec(
        "printf 'ip='; command -v ip || true; printf 'nft='; command -v nft || true",
        timeout=15,
    )
    cgroup = await bench.exec(
        "test -w /sys/fs/cgroup && echo writable || echo not-writable",
        timeout=15,
    )
    return {
        "tool_probe": result_block(tools),
        "target_lacks_ip": "ip=\n" in _text(tools, "stdout") or _text(tools, "stdout").startswith("ip=nft="),
        "target_lacks_nft": _text(tools, "stdout").rstrip().endswith("nft="),
        "cgroup_writable": _text(cgroup, "stdout").strip() == "writable",
    }


async def host_network_state(bench: DockerBench) -> dict[str, Any]:
    result = await bench.exec(
        "ls -1 /sys/class/net 2>/dev/null | awk '/^eos-shared0$/ || /^eos-iws-/ {print}'",
        timeout=15,
    )
    names = [line.strip() for line in _text(result, "stdout").splitlines() if line.strip()]
    return {
        "bridge_exists": "eos-shared0" in names,
        "host_veth_names": [name for name in names if name.startswith("eos-iws-")],
        "names": names,
    }


async def read_audit(bench: DockerBench) -> dict[str, Any]:
    result = await bench.exec(f"cat {shlex.quote(AUDIT_PATH)}", timeout=15)
    if _exit_code(result) != 0:
        return {"readable": False, "error": result_block(result), "events": []}
    events = []
    for line in _text(result, "stdout").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"decode_error": line})
    return {"readable": True, "events": events}


async def path_exists(bench: DockerBench, path: str) -> bool:
    result = await bench.exec(f"test -e {shlex.quote(path)}", timeout=15)
    return _exit_code(result) == 0


async def process_exists(bench: DockerBench, pid: int | None) -> bool | None:
    if pid is None:
        return None
    result = await bench.exec(f"test -d /proc/{int(pid)}", timeout=15)
    return _exit_code(result) == 0


def decode_response(result: Any) -> dict[str, Any]:
    text = _text(result, "stdout").strip()
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"daemon returned invalid JSON: {text!r}") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"daemon returned non-object JSON: {decoded!r}")
    return decoded


def read_exists(response: dict[str, Any]) -> bool:
    if response.get("exists") is False:
        return False
    if response.get("success") is False and nested(response, "error", "kind") in {
        "not_found",
        "file_not_found",
    }:
        return False
    content = response.get("content")
    return isinstance(content, str) and bool(content)


def combined_output(response: dict[str, Any]) -> str:
    output = response.get("output")
    if isinstance(output, dict):
        return f"{output.get('stdout', '')}{output.get('stderr', '')}"
    return f"{response.get('stdout', '')}{response.get('stderr', '')}"


def trim_response(response: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "success",
        "status",
        "exit_code",
        "workspace",
        "workspace_mode",
        "pty_session_id",
        "changed_paths",
        "error",
        "exists",
    )
    trimmed = {key: response.get(key) for key in keys if key in response}
    output = response.get("output")
    if isinstance(output, dict):
        trimmed["output"] = {
            "stdout": str(output.get("stdout", ""))[-700:],
            "stderr": str(output.get("stderr", ""))[-700:],
        }
    if "content" in response:
        trimmed["content"] = str(response.get("content", ""))[-200:]
    return trimmed


def trim_exit_response(response: dict[str, Any]) -> dict[str, Any]:
    trimmed = trim_response(response)
    for key in (
        "force_cancel_requested",
        "force_cancelled_pty_session_ids",
        "stale_pty_session_ids",
        "active_pty_session_ids_after",
        "evicted_upperdir_bytes",
        "inspection",
    ):
        if key in response:
            trimmed[key] = response[key]
    return trimmed


def nested(value: Any, *keys: str, default: Any = None) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def result_block(result: Any) -> dict[str, Any]:
    return {
        "exit_code": _exit_code(result),
        "stdout": _text(result, "stdout").strip(),
        "stderr": _text(result, "stderr").strip(),
    }


@contextmanager
def optional_env(key: str, value: str | None):
    if value is None:
        yield
        return
    previous = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


if __name__ == "__main__":
    raise SystemExit(main())
