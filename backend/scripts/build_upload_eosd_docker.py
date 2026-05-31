#!/usr/bin/env python3
"""Build/package local ``eosd`` and verify Docker image upload.

The target container is treated as a minimal Linux runtime: no Rust toolchain,
no package install, no shell during artifact verification. The script builds on
the host via ``sandbox/xtask``, streams the binary into Docker with
``put_archive``, reads it back with Docker's archive API, then direct-execs
``eosd --version``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SANDBOX_DIR = ROOT / "sandbox"

TARGETS = {
    "amd64": {
        "target": "x86_64-unknown-linux-musl",
        "artifact": "eosd-linux-amd64",
        "platform": "linux/amd64",
        "image": "sweevo-dask__dask-10042:latest",
    },
    "arm64": {
        "target": "aarch64-unknown-linux-musl",
        "artifact": "eosd-linux-arm64",
        "platform": "linux/arm64",
        "image": "python:3.11-slim",
    },
}


@dataclass(frozen=True)
class ExecProbe:
    exit_code: int | None
    stdout: str
    stderr: str
    error: str | None = None

    @property
    def present(self) -> bool:
        return self.exit_code == 0

    def to_json(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "exit_code": self.exit_code,
            "stdout": self.stdout.strip(),
            "stderr": self.stderr.strip(),
            "error": self.error,
        }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    target_cfg = TARGETS[args.arch]
    out_dir = Path(args.out_dir)
    artifact_path = out_dir / target_cfg["artifact"]

    if not args.no_build:
        run_xtask_package(
            target=str(target_cfg["target"]),
            out_dir=out_dir,
            builder=args.builder,
        )
    if not artifact_path.exists():
        raise SystemExit(f"missing artifact {artifact_path}; rerun without --no-build")

    report = verify_upload(
        image=args.image or str(target_cfg["image"]),
        platform=args.platform or str(target_cfg["platform"]),
        artifact_path=artifact_path,
        remote_path=args.remote_path,
        keep_container=args.keep_container,
        container_command=args.container_command,
    )
    report.update(
        {
            "arch": args.arch,
            "target": target_cfg["target"],
            "builder": args.builder,
            "built": not args.no_build,
            "no_package_install_in_target": True,
            "target_requires_rust": False,
        }
    )

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    artifact = report["artifact"]
    print(
        f"wrote {report_path} "
        f"(arch={args.arch} upload={artifact['upload_time_ms']:.3f}ms "
        f"gate_pass={artifact['gate_pass']})"
    )
    return 0 if artifact["gate_pass"] else 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=sorted(TARGETS), default="amd64")
    parser.add_argument(
        "--image",
        help="Docker image to create for upload verification. Defaults by --arch.",
    )
    parser.add_argument(
        "--platform",
        help="Docker platform, e.g. linux/amd64 or linux/arm64. Defaults by --arch.",
    )
    parser.add_argument(
        "--builder",
        default="rust-lld",
        choices=("rust-lld", "cargo", "cross"),
        help="xtask package builder.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(SANDBOX_DIR / "dist"),
        help="Artifact output directory.",
    )
    parser.add_argument(
        "--remote-path",
        default="/tmp/eosd-local/eosd",
        help="Path to place eosd inside the target container.",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="JSON report path. Defaults to bench/local-eosd-<arch>-upload.json.",
    )
    parser.add_argument("--no-build", action="store_true", help="Upload an existing artifact.")
    parser.add_argument(
        "--keep-container",
        action="store_true",
        help="Leave the temporary verification container running.",
    )
    parser.add_argument(
        "--container-command",
        nargs="+",
        default=["sleep", "infinity"],
        help="Command used to keep the verification container alive.",
    )
    args = parser.parse_args(argv)
    if args.report is None:
        args.report = str(ROOT / "bench" / f"local-eosd-{args.arch}-upload.json")
    return args


def run_xtask_package(*, target: str, out_dir: Path, builder: str) -> None:
    command = [
        "cargo",
        "run",
        "-p",
        "xtask",
        "--",
        "package",
        "--target",
        target,
        "--out-dir",
        str(out_dir),
        "--builder",
        builder,
    ]
    subprocess.run(command, cwd=SANDBOX_DIR, check=True)


def verify_upload(
    *,
    image: str,
    platform: str,
    artifact_path: Path,
    remote_path: str,
    keep_container: bool,
    container_command: list[str],
) -> dict[str, Any]:
    try:
        import docker  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit("docker SDK is required: install the project docker extra") from exc

    client = docker.from_env()
    container = None
    name = f"eos-eosd-upload-{uuid.uuid4().hex[:10]}"
    started = time.perf_counter()
    try:
        container = client.containers.create(
            image=image,
            name=name,
            command=container_command,
            detach=True,
            platform=platform,
            labels={"purpose": "eosd-local-upload-verify"},
        )
        container.start()
        container.reload()

        local_bytes = artifact_path.read_bytes()
        local_sha = hashlib.sha256(local_bytes).hexdigest()
        upload_start = time.perf_counter()
        ok = container.put_archive(
            path="/",
            data=tar_file_at_path(remote_path, local_bytes, mode=0o755),
        )
        upload_ms = elapsed_ms(upload_start)
        if not ok:
            raise RuntimeError("docker put_archive returned False")

        remote_bytes, remote_mode = read_remote_file(container, remote_path)
        remote_sha = hashlib.sha256(remote_bytes).hexdigest()
        version = exec_probe(container, [remote_path, "--version"])

        return {
            "image": image,
            "platform": platform,
            "container_id": container.id,
            "container_name": name,
            "container_command": container_command,
            "container_start_ms": elapsed_ms(started),
            "toolchain_probe": {
                "rustc": exec_probe(container, ["rustc", "--version"]).to_json(),
                "cargo": exec_probe(container, ["cargo", "--version"]).to_json(),
            },
            "artifact": {
                "source_path": str(artifact_path),
                "remote_path": remote_path,
                "size_bytes": len(local_bytes),
                "upload_time_ms": upload_ms,
                "local_sha256": local_sha,
                "remote_sha256": remote_sha,
                "hashes_match": local_sha == remote_sha,
                "remote_mode": oct(remote_mode),
                "executable": bool(remote_mode & 0o111),
                "version": version.to_json(),
                "gate_pass": (
                    local_sha == remote_sha
                    and bool(remote_mode & 0o111)
                    and version.exit_code == 0
                ),
            },
        }
    finally:
        if container is not None and not keep_container:
            container.remove(force=True)


def exec_probe(container: Any, argv: list[str]) -> ExecProbe:
    try:
        exit_code, output = container.exec_run(cmd=argv, demux=True, tty=False)
    except Exception as exc:
        return ExecProbe(exit_code=None, stdout="", stderr="", error=str(exc))
    stdout_b, stderr_b = docker_output_bytes(output)
    return ExecProbe(
        exit_code=int(exit_code or 0),
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
    )


def read_remote_file(container: Any, path: str) -> tuple[bytes, int]:
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


def tar_file_at_path(path: str, payload: bytes, *, mode: int) -> bytes:
    relative = path.strip("/")
    if not relative or relative.startswith("../") or "/../" in f"/{relative}/":
        raise ValueError(f"invalid remote path {path!r}")
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


def docker_output_bytes(output: object) -> tuple[bytes, bytes]:
    if isinstance(output, tuple) and len(output) == 2:
        return bytes(output[0] or b""), bytes(output[1] or b"")
    if isinstance(output, (bytes, bytearray)):
        return bytes(output), b""
    return b"", b""


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


if __name__ == "__main__":
    sys.exit(main())
