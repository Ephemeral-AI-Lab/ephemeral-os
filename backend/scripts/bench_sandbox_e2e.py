"""Sandbox benchmark driver for synthetic checks and Rust-migration CP-0/CP-1.

Default mode remains the historical self-contained synthetic loop:

    bench_sandbox_e2e.py --commands 10 --report synthetic.json

Phase-0 mode uses the Docker provider against an existing container or a newly
created image-backed sandbox. It captures the in-sandbox Python baseline (CP-0)
and optionally compares provider ``put_archive`` with base64-over-exec upload
(CP-1):

    bench_sandbox_e2e.py --docker-image sweevo-dask__dask-10042 --phase0 \
      --commands 10 --report bench/baseline-amd64.json

Local artifact mode verifies the Phase-0 handoff path without installing
packages inside the sandbox image:

    bench_sandbox_e2e.py --docker-image sweevo-dask__dask-10042 \
      --eosd-binary sandbox/dist/eosd-linux-amd64 \
      --report bench/local-eosd-amd64-upload.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import platform
import statistics
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass
from posixpath import normpath
from pathlib import Path
from typing import Any, Callable

BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

DEFAULT_DOCKER_IMAGE = "sweevo-dask__dask-10042"
DEFAULT_PAYLOAD_MIB = (1.5, 3.0)


def _default_iteration() -> float:
    """Run one synthetic svc.cmd iteration; return elapsed seconds.

    The scaffold uses an in-process no-op loop sized to approximate the
    overlay cost breakdown documented in memory
    ``codeact_overlay_cost_breakdown.md`` (``_commit_changes`` ~0.65s,
    ``overlay_run`` ~0.43s). This keeps the harness self-contained when no
    real provider is wired — real-provider regression must replace this
    callable.
    """
    t0 = time.perf_counter()
    # ~10ms scaffold workload — meant to be visible in p50/p95 without
    # blowing CI budget. Real harness substitutes this for a daemon RPC.
    n = 0
    for _ in range(50_000):
        n += 1
    return time.perf_counter() - t0


def run_bench(
    commands: int,
    *,
    iteration: Callable[[], float] = _default_iteration,
) -> dict[str, float | int | str]:
    samples_ms: list[float] = []
    for _ in range(commands):
        samples_ms.append(iteration() * 1000.0)
    samples_ms.sort()
    return {
        "commands": commands,
        "svc_cmd_p50": statistics.median(samples_ms),
        "svc_cmd_p95": samples_ms[int(0.95 * (len(samples_ms) - 1))],
        "svc_cmd_min": samples_ms[0],
        "svc_cmd_max": samples_ms[-1],
        "samples_ms": samples_ms,
    }


@dataclass
class DockerBench:
    adapter: Any
    sandbox_id: str
    created: bool

    @classmethod
    async def create(
        cls,
        *,
        image: str | None,
        container_id: str | None,
        name_prefix: str,
        platform: str | None = None,
    ) -> "DockerBench":
        from sandbox.provider.docker.adapter import DockerProviderAdapter
        from sandbox.provider.registry import register_adapter

        adapter = DockerProviderAdapter()
        if container_id:
            sandbox_id = container_id
            created = False
        else:
            image_ref = image or DEFAULT_DOCKER_IMAGE
            name = f"{name_prefix}-{uuid.uuid4().hex[:10]}"
            sandbox = await asyncio.to_thread(
                adapter.create,
                name=name,
                image=image_ref,
                labels={"purpose": "sandbox-rust-phase0-bench"},
                platform=platform,
            )
            sandbox_id = str(sandbox["id"])
            created = True
        register_adapter(sandbox_id, adapter)
        return cls(adapter=adapter, sandbox_id=sandbox_id, created=created)

    async def close(self, *, keep: bool) -> None:
        from sandbox.provider.registry import dispose_adapter

        try:
            if self.created and not keep:
                await asyncio.to_thread(self.adapter.delete, self.sandbox_id)
        finally:
            dispose_adapter(self.sandbox_id)

    async def exec(self, command: str, *, timeout: int | None = None) -> Any:
        return await self.adapter.exec(self.sandbox_id, command, timeout=timeout)

    async def direct_exec(self, argv: list[str], *, timeout: int | None = None) -> Any:
        from sandbox.shared.models import RawExecResult

        def _run() -> RawExecResult:
            client = self.adapter._get_client()  # Docker-only benchmark helper.
            container = client.containers.get(self.sandbox_id)
            exit_code, output = container.exec_run(cmd=argv, demux=True, tty=False)
            stdout_b, stderr_b = _docker_output_bytes(output)
            return RawExecResult(
                success=int(exit_code or 0) == 0,
                exit_code=int(exit_code or 0),
                stdout=stdout_b.decode("utf-8", errors="replace"),
                stderr=stderr_b.decode("utf-8", errors="replace"),
            )

        if timeout is not None:
            return await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
        return await asyncio.to_thread(_run)

    async def get_file_archive(self, path: str, *, timeout: int | None = None) -> tuple[bytes, int]:
        def _run() -> tuple[bytes, int]:
            client = self.adapter._get_client()  # Docker-only benchmark helper.
            container = client.containers.get(self.sandbox_id)
            chunks, _stat = container.get_archive(path)
            raw = b"".join(chunks)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
                member = next((item for item in tar.getmembers() if item.isfile()), None)
                if member is None:
                    raise RuntimeError(f"archive for {path} did not contain a file")
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise RuntimeError(f"archive member {member.name} could not be read")
                return extracted.read(), member.mode

        if timeout is not None:
            return await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
        return await asyncio.to_thread(_run)


async def run_docker_phase0(
    *,
    image: str | None,
    container_id: str | None,
    keep_container: bool,
    commands: int,
    include_cp0: bool,
    include_cp1: bool,
    payload_mib: list[float],
    put_archive_ratio_limit: float,
    eosd_binary: Path | None,
    eosd_dest_dir: str,
    name_prefix: str,
) -> dict[str, Any]:
    bench = await DockerBench.create(
        image=image,
        container_id=container_id,
        name_prefix=name_prefix,
    )
    try:
        report: dict[str, Any] = {
            "scaffold": False,
            "mode": "docker-phase0" if include_cp0 else "docker-upload",
            "sandbox_id": bench.sandbox_id,
            "created_container": bench.created,
            "host": {
                "platform": platform.platform(),
                "python": sys.version.split()[0],
            },
            "environment": await collect_environment(bench),
        }
        if include_cp0:
            report["cp0"] = await measure_cp0(bench, commands=commands)
        if include_cp1:
            report["cp1"] = await measure_cp1(
                bench,
                payload_mib=payload_mib,
                put_archive_ratio_limit=put_archive_ratio_limit,
            )
        if eosd_binary is not None:
            report["local_artifact"] = await verify_local_eosd_upload(
                bench,
                eosd_binary=eosd_binary,
                dest_dir=eosd_dest_dir,
            )
        return report
    finally:
        await bench.close(keep=keep_container)


async def collect_environment(bench: DockerBench) -> dict[str, Any]:
    probes = {
        "kernel": "uname -r",
        "architecture": "uname -m",
        "os_release": "cat /etc/os-release 2>/dev/null || true",
        "python": "python3 --version 2>&1 || true",
        "rustc": "if command -v rustc >/dev/null 2>&1; then rustc --version; else echo missing; fi",
        "cargo": "if command -v cargo >/dev/null 2>&1; then cargo --version; else echo missing; fi",
        "userns_clone": "cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || true",
        "max_user_namespaces": "cat /proc/sys/user/max_user_namespaces 2>/dev/null || true",
        "overlay_filesystem": "grep -w overlay /proc/filesystems 2>/dev/null || true",
        "eos_mount": "findmnt -T /eos -no FSTYPE,OPTIONS 2>/dev/null || true",
    }
    out: dict[str, Any] = {}
    for name, command in probes.items():
        result = await bench.exec(command, timeout=15)
        out[name] = {
            "exit_code": _exit_code(result),
            "stdout": _text(result, "stdout").strip(),
            "stderr": _text(result, "stderr").strip(),
        }
    out["overlay_in_userns_probe"] = await overlay_in_userns_probe(bench)
    return out


async def overlay_in_userns_probe(bench: DockerBench) -> dict[str, Any]:
    command = r"""
set -eu
mkdir -p /eos/mount
root="$(mktemp -d /eos/mount/eos-overlay-probe.XXXXXX)"
cleanup() { rm -rf "$root"; }
trap cleanup EXIT
mkdir -p "$root/lower" "$root/upper" "$root/work" "$root/merged"
unshare -Ur -m sh -c '
  root="$1"
  mount -t overlay overlay \
    -o "lowerdir=$root/lower,upperdir=$root/upper,workdir=$root/work" \
    "$root/merged"
  umount "$root/merged"
' sh "$root"
"""
    samples = await time_exec(bench, command, samples=3, timeout=30, allow_failure=True)
    return {
        "success": samples["exit_code"] == 0,
        **samples,
    }


async def measure_cp0(bench: DockerBench, *, commands: int) -> dict[str, Any]:
    from sandbox.host.daemon_client import call_daemon_api, ensure_daemon_current
    from sandbox.host.runtime_bundle import ensure_runtime_uploaded

    upload_start = time.perf_counter()
    bundle_sha = await ensure_runtime_uploaded(bench.sandbox_id)
    upload_ms = elapsed_ms(upload_start)

    await kill_python_daemon(bench)

    cold_start = time.perf_counter()
    await ensure_daemon_current(bench.sandbox_id)
    daemon_cold_start_ms = elapsed_ms(cold_start)
    daemon_rss_kb = await read_daemon_rss_kb(bench)

    runtime_init = await time_exec(
        bench,
        "python3 -c 'pass'",
        samples=max(3, min(commands, 10)),
        timeout=15,
        allow_failure=False,
    )

    warm_samples_ms: list[float] = []
    for _ in range(commands):
        started = time.perf_counter()
        await call_daemon_api(
            bench.sandbox_id,
            "api.v1.heartbeat",
            {"invocation_ids": []},
            timeout=30,
        )
        warm_samples_ms.append(elapsed_ms(started))

    return {
        "commands": commands,
        "runtime_bundle_sha": bundle_sha,
        "upload_time_ms": upload_ms,
        "daemon_cold_start_ms": daemon_cold_start_ms,
        "daemon_idle_rss_kb": daemon_rss_kb,
        "python_process_start_ms": runtime_init,
        "warm_heartbeat_ms": summarize_samples(warm_samples_ms),
    }


async def kill_python_daemon(bench: DockerBench) -> None:
    command = r"""
pid_file=/eos/daemon/runtime.pid
if [ -f "$pid_file" ]; then
  kill "$(cat "$pid_file")" 2>/dev/null || true
fi
rm -f /eos/daemon/runtime.sock "$pid_file" /eos/daemon/runtime.env
"""
    result = await bench.exec(command, timeout=15)
    if _exit_code(result) != 0:
        raise RuntimeError(f"failed to reset daemon: {_combined_output(result)}")


async def read_daemon_rss_kb(bench: DockerBench) -> int | None:
    result = await bench.exec(
        "pid=$(cat /eos/daemon/runtime.pid); ps -o rss= -p \"$pid\"",
        timeout=15,
    )
    if _exit_code(result) != 0:
        return None
    text = _text(result, "stdout").strip()
    try:
        return int(text)
    except ValueError:
        return None


async def measure_cp1(
    bench: DockerBench,
    *,
    payload_mib: list[float],
    put_archive_ratio_limit: float,
) -> dict[str, Any]:
    from sandbox.host.chunked_upload import write_base64_chunks

    rows: list[dict[str, Any]] = []
    for mib in payload_mib:
        size = int(mib * 1024 * 1024)
        payload = deterministic_payload(size)
        expected_sha = hashlib.sha256(payload).hexdigest()
        name = f"payload-{size}.bin"

        put_dir = f"/tmp/eos-phase0-put-{uuid.uuid4().hex[:8]}"
        await require_success(bench, f"mkdir -p {put_dir}", "create put_archive dest")
        put_start = time.perf_counter()
        await bench.adapter.put_archive(
            bench.sandbox_id,
            tar_stream=tar_single_file(name, payload),
            dest_dir=put_dir,
        )
        put_ms = elapsed_ms(put_start)
        put_sha = await remote_sha256(bench, f"{put_dir}/{name}")

        base64_dir = f"/tmp/eos-phase0-b64-{uuid.uuid4().hex[:8]}"
        await require_success(bench, f"mkdir -p {base64_dir}", "create base64 dest")
        remote_path = f"{base64_dir}/{name}"
        base64_start = time.perf_counter()
        chunks = await write_base64_chunks(
            bench.adapter.exec,
            bench.sandbox_id,
            content=payload,
            remote_path=remote_path,
            check_result=lambda result, message: _check_success(result, message),
            failure_message=lambda offset: f"base64 chunk failed at offset {offset}",
        )
        base64_ms = elapsed_ms(base64_start)
        base64_sha = await remote_sha256(bench, remote_path)

        rows.append(
            {
                "size_bytes": size,
                "put_archive_ms": put_ms,
                "base64_exec_ms": base64_ms,
                "base64_chunks": chunks,
                "expected_sha256": expected_sha,
                "put_archive_sha256": put_sha,
                "base64_sha256": base64_sha,
                "hashes_match": put_sha == expected_sha and base64_sha == expected_sha,
                "put_archive_no_slower_than_base64": put_ms <= base64_ms,
            }
        )

    put_times = [float(row["put_archive_ms"]) for row in rows]
    ratio = max(put_times) / min(put_times) if put_times and min(put_times) > 0 else None
    gate_pass = (
        all(bool(row["hashes_match"]) for row in rows)
        and all(bool(row["put_archive_no_slower_than_base64"]) for row in rows)
        and ratio is not None
        and ratio <= put_archive_ratio_limit
    )
    return {
        "payload_mib": payload_mib,
        "put_archive_size_ratio_limit": put_archive_ratio_limit,
        "put_archive_size_ratio": ratio,
        "gate_pass": gate_pass,
        "samples": rows,
    }


async def verify_local_eosd_upload(
    bench: DockerBench,
    *,
    eosd_binary: Path,
    dest_dir: str,
) -> dict[str, Any]:
    payload = eosd_binary.read_bytes()
    expected_sha = hashlib.sha256(payload).hexdigest()
    remote_path = f"{dest_dir.rstrip('/')}/eosd"

    started = time.perf_counter()
    await bench.adapter.put_archive(
        bench.sandbox_id,
        tar_stream=tar_file_at_path(remote_path, payload, mode=0o755),
        dest_dir="/",
    )
    upload_ms = elapsed_ms(started)

    remote_bytes, remote_mode = await bench.get_file_archive(remote_path, timeout=60)
    remote_sha = hashlib.sha256(remote_bytes).hexdigest()
    version = await bench.direct_exec([remote_path, "--version"], timeout=30)

    return {
        "source_path": str(eosd_binary),
        "dest_dir": dest_dir,
        "remote_path": remote_path,
        "size_bytes": len(payload),
        "upload_time_ms": upload_ms,
        "local_sha256": expected_sha,
        "remote_sha256": remote_sha,
        "hashes_match": remote_sha == expected_sha,
        "remote_mode": oct(remote_mode),
        "executable": bool(remote_mode & 0o111),
        "version": {
            "exit_code": _exit_code(version),
            "stdout": _text(version, "stdout").strip(),
            "stderr": _text(version, "stderr").strip(),
        },
        "gate_pass": (
            remote_sha == expected_sha
            and bool(remote_mode & 0o111)
            and _exit_code(version) == 0
        ),
    }


async def time_exec(
    bench: DockerBench,
    command: str,
    *,
    samples: int,
    timeout: int,
    allow_failure: bool,
) -> dict[str, Any]:
    durations: list[float] = []
    last_result: Any = None
    for _ in range(samples):
        started = time.perf_counter()
        last_result = await bench.exec(command, timeout=timeout)
        durations.append(elapsed_ms(started))
        if _exit_code(last_result) != 0:
            if allow_failure:
                break
            raise RuntimeError(f"bench command failed: {_combined_output(last_result)}")
    result_exit = _exit_code(last_result) if last_result is not None else 0
    return {
        "exit_code": result_exit,
        "samples": summarize_samples(durations),
        "stdout": _text(last_result, "stdout").strip() if last_result is not None else "",
        "stderr": _text(last_result, "stderr").strip() if last_result is not None else "",
    }


async def require_success(bench: DockerBench, command: str, message: str) -> None:
    result = await bench.exec(command, timeout=30)
    _check_success(result, message)


async def remote_sha256(bench: DockerBench, path: str) -> str:
    script = (
        "import hashlib, pathlib, sys; "
        "print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())"
    )
    quoted_path = _shell_quote(path)
    quoted_script = _shell_quote(script)
    result = await bench.exec(
        "if command -v sha256sum >/dev/null 2>&1; then "
        f"sha256sum {quoted_path} | cut -d ' ' -f 1; "
        "else "
        f"python3 -c {quoted_script} {quoted_path}; "
        "fi",
        timeout=60,
    )
    _check_success(result, f"hash {path}")
    return _text(result, "stdout").strip()


def deterministic_payload(size: int) -> bytes:
    seed = b"eos phase0 upload payload\n"
    return (seed * ((size // len(seed)) + 1))[:size]


def tar_single_file(name: str, payload: bytes, *, mode: int = 0o644) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mode = mode
        tar.addfile(info, io.BytesIO(payload))
    return raw.getvalue()


def tar_file_at_path(path: str, payload: bytes, *, mode: int = 0o644) -> bytes:
    relative = normpath(path).lstrip("/")
    if relative in {"", "."} or relative.startswith("../"):
        raise ValueError(f"invalid archive path {path!r}")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        parent = ""
        for part in Path(relative).parent.parts:
            parent = f"{parent}/{part}" if parent else part
            info = tarfile.TarInfo(parent)
            info.type = tarfile.DIRTYPE
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o755
            tar.addfile(info)
        info = tarfile.TarInfo(relative)
        info.size = len(payload)
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mode = mode
        tar.addfile(info, io.BytesIO(payload))
    return raw.getvalue()


def _docker_output_bytes(output: object) -> tuple[bytes, bytes]:
    if isinstance(output, tuple) and len(output) == 2:
        return bytes(output[0] or b""), bytes(output[1] or b"")
    if isinstance(output, (bytes, bytearray)):
        return bytes(output), b""
    return b"", b""


def summarize_samples(samples_ms: list[float]) -> dict[str, Any]:
    if not samples_ms:
        return {"count": 0, "samples_ms": []}
    ordered = sorted(samples_ms)
    return {
        "count": len(ordered),
        "p50": statistics.median(ordered),
        "p95": ordered[int(0.95 * (len(ordered) - 1))],
        "min": ordered[0],
        "max": ordered[-1],
        "samples_ms": ordered,
    }


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _check_success(result: object, message: str) -> None:
    if _exit_code(result) != 0:
        raise RuntimeError(f"{message}: {_combined_output(result)}")


def _exit_code(result: object) -> int:
    raw = getattr(result, "exit_code", None)
    if raw is None:
        return 1
    return int(raw)


def _text(result: object, attr: str) -> str:
    value = getattr(result, attr, "")
    return value if isinstance(value, str) else str(value or "")


def _combined_output(result: object) -> str:
    stdout = _text(result, "stdout").strip()
    stderr = _text(result, "stderr").strip()
    return "\n".join(part for part in (stdout, stderr) if part)


def _shell_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commands",
        type=int,
        default=10,
        help="Number of synthetic svc.cmd iterations to sample.",
    )
    parser.add_argument(
        "--report",
        type=str,
        required=True,
        help="Path to write JSON report (overwrites if exists).",
    )
    parser.add_argument(
        "--docker-image",
        default=None,
        help=f"Run CP-0/CP-1 in a newly created Docker sandbox image (default: {DEFAULT_DOCKER_IMAGE}).",
    )
    parser.add_argument(
        "--container-id",
        default=None,
        help="Run CP-0/CP-1 against an existing Docker container id/name.",
    )
    parser.add_argument(
        "--phase0",
        action="store_true",
        help="With Docker mode, run both CP-0 and CP-1. Otherwise Docker mode runs CP-0 only.",
    )
    parser.add_argument(
        "--cp1",
        action="store_true",
        help="With Docker mode, include the CP-1 upload comparison.",
    )
    parser.add_argument(
        "--payload-mib",
        type=float,
        nargs="+",
        default=list(DEFAULT_PAYLOAD_MIB),
        help="CP-1 payload sizes in MiB.",
    )
    parser.add_argument(
        "--put-archive-ratio-limit",
        type=float,
        default=2.5,
        help="Maximum allowed max/min put_archive time ratio across CP-1 payload sizes.",
    )
    parser.add_argument(
        "--keep-container",
        action="store_true",
        help="Do not delete a container created by --docker-image.",
    )
    parser.add_argument(
        "--eosd-binary",
        type=Path,
        default=None,
        help="Locally built eosd binary to upload and verify with put_archive.",
    )
    parser.add_argument(
        "--eosd-dest-dir",
        default="/tmp/eosd-local",
        help="Destination directory inside the sandbox for --eosd-binary.",
    )
    parser.add_argument(
        "--name-prefix",
        default="eos-phase0-bench",
        help="Name prefix for containers created by --docker-image.",
    )
    args = parser.parse_args(argv)

    run_id = os.environ.get("EOS_TIER_RUN_ID") or f"local-{uuid.uuid4().hex[:12]}"

    if args.docker_image or args.container_id or args.phase0 or args.cp1 or args.eosd_binary:
        include_cp0 = args.eosd_binary is None or args.phase0 or args.cp1
        report = asyncio.run(
            run_docker_phase0(
                image=args.docker_image or DEFAULT_DOCKER_IMAGE,
                container_id=args.container_id,
                keep_container=args.keep_container,
                commands=args.commands,
                include_cp0=include_cp0,
                include_cp1=args.phase0 or args.cp1,
                payload_mib=args.payload_mib,
                put_archive_ratio_limit=args.put_archive_ratio_limit,
                eosd_binary=args.eosd_binary,
                eosd_dest_dir=args.eosd_dest_dir,
                name_prefix=args.name_prefix,
            )
        )
    else:
        report = run_bench(args.commands)
        report["scaffold"] = True
        report["mode"] = "synthetic"
    report["run_id"] = run_id

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    if report.get("mode") == "synthetic":
        print(
            f"wrote {out} (p50={report['svc_cmd_p50']:.3f}ms "
            f"p95={report['svc_cmd_p95']:.3f}ms run_id={run_id})"
        )
    elif "cp0" in report:
        cp0 = report["cp0"]
        print(
            f"wrote {out} (upload={cp0['upload_time_ms']:.3f}ms "
            f"cold={cp0['daemon_cold_start_ms']:.3f}ms run_id={run_id})"
        )
    else:
        artifact = report["local_artifact"]
        print(
            f"wrote {out} (eosd_upload={artifact['upload_time_ms']:.3f}ms "
            f"gate_pass={artifact['gate_pass']} run_id={run_id})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
