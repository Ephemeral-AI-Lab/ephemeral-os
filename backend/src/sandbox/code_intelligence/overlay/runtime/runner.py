"""CLI orchestration for the sandbox-side overlay runtime."""

from __future__ import annotations

import argparse
import base64
import json
import os
import stat
import subprocess
import sys
import time
from collections.abc import Iterator

from .mounts import _NS_LOWER, _NS_UPPER, OverlayMountError, setup_mounts
from .ndjson import write_diff_ndjson
from .types import UpperChange, UpperChangeKind, UpperEntry

REJECT_UPPER_FULL = "overlay_upper_full"
_REJECT_EXIT_BASE = 200


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--upper-size-mb", type=int, required=True)
    parser.add_argument(
        "--user-cmd-b64",
        required=True,
        help="Base64-encoded bash command to run inside the overlay.",
    )
    parser.add_argument(
        "--stdin-b64",
        default="",
        help="Optional base64-encoded stdin payload for the user command.",
    )
    return parser.parse_args(argv)


def _write_result_json(
    *,
    run_dir: str,
    exit_code: int,
    rejected: dict[str, object] | None,
    run_timings: dict[str, float],
) -> str:
    path = os.path.join(run_dir, "result.json")
    tmp_path = f"{path}.tmp-{os.getpid()}"
    payload = {
        "exit_code": exit_code,
        "rejected": rejected,
        "run_timings": dict(run_timings),
    }
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    return path


def _record_timing(timings: dict[str, float], key: str, started_at: float) -> None:
    timings[key] = round(time.perf_counter() - started_at, 6)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - e2e path
    total_started = time.perf_counter()
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    workspace_root = args.workspace_root.rstrip("/")
    run_dir = args.run_dir.rstrip("/")
    os.makedirs(run_dir, exist_ok=True)

    run_timings: dict[str, float] = {}

    try:
        setup_started = time.perf_counter()
        setup_mounts(live_root=workspace_root, upper_size_mb=args.upper_size_mb)
        _record_timing(run_timings, "setup_mounts", setup_started)
    except OverlayMountError as exc:
        print(str(exc), file=sys.stderr)
        return 255

    decode_started = time.perf_counter()
    user_cmd = base64.b64decode(args.user_cmd_b64).decode("utf-8")
    stdin_bytes = base64.b64decode(args.stdin_b64) if args.stdin_b64 else None
    _record_timing(run_timings, "decode_command", decode_started)

    user_started = time.perf_counter()
    stdout_path = os.path.join(run_dir, "stdout.bin")
    _stdout_bytes, exit_code = run_user_command(
        user_cmd=user_cmd,
        stdin_bytes=stdin_bytes,
        cwd=workspace_root,
        stdout_path=stdout_path,
    )
    _record_timing(run_timings, "user_command", user_started)

    walk_started = time.perf_counter()
    upper_entries = list(walk_upperdir(_NS_UPPER))
    upper_changes = tuple(_build_upper_change(entry) for entry in upper_entries)
    upper_bytes = sum(len(change.upper_bytes or b"") for change in upper_changes)
    _record_timing(run_timings, "walk_upperdir", walk_started)
    run_timings["total"] = round(time.perf_counter() - total_started, 6)

    write_diff_ndjson(
        run_dir=run_dir,
        exit_code=exit_code,
        upper_changes=upper_changes,
        upper_bytes=upper_bytes,
        upper_files=len(upper_changes),
        run_timings=run_timings,
    )
    _write_result_json(
        run_dir=run_dir,
        exit_code=exit_code,
        rejected=None,
        run_timings=run_timings,
    )
    return exit_code


def run_user_command(
    *, user_cmd: str, stdin_bytes: bytes | None, cwd: str, stdout_path: str
) -> tuple[bytes, int]:
    """Run the user command under the merged overlay view."""
    proc = subprocess.Popen(
        ["bash", "-o", "pipefail", "-lc", user_cmd],
        stdin=subprocess.PIPE if stdin_bytes is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if stdin_bytes is not None:
        assert proc.stdin is not None
        proc.stdin.write(stdin_bytes)
        proc.stdin.close()
    assert proc.stdout is not None
    chunks: list[bytes] = []
    with open(stdout_path, "wb") as stdout_file:
        while True:
            chunk = os.read(proc.stdout.fileno(), 8192)
            if not chunk:
                break
            chunks.append(chunk)
            stdout_file.write(chunk)
            stdout_file.flush()
    exit_code = proc.wait()
    return b"".join(chunks), exit_code


def walk_upperdir(upper_root: str) -> Iterator[UpperEntry]:
    """Yield one upperdir entry per captured overlay mutation."""
    upper_root = upper_root.rstrip("/")
    if not os.path.isdir(upper_root):
        return
    for dirpath, dirnames, filenames in os.walk(
        upper_root, topdown=True, followlinks=False
    ):
        rel_dir = os.path.relpath(dirpath, upper_root)
        rel_dir = "" if rel_dir == "." else rel_dir

        if rel_dir:
            full = os.path.join(upper_root, rel_dir)
            try:
                st = os.lstat(full)
            except FileNotFoundError:
                pass
            else:
                xattrs = _read_xattrs(full)
                if is_opaque_dir(st, xattrs):
                    yield UpperEntry(
                        rel=rel_dir, st=st, xattrs=xattrs, upper_path=full
                    )

        for name in filenames:
            rel = os.path.join(rel_dir, name) if rel_dir else name
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except FileNotFoundError:
                continue
            yield UpperEntry(
                rel=rel,
                st=st,
                xattrs=_read_xattrs(full),
                upper_path=full,
            )

        dirnames.sort()


def _build_upper_change(entry: UpperEntry) -> UpperChange:
    kind = _entry_kind(entry)
    base_bytes = _read_base_bytes(entry.rel)
    upper_bytes: bytes | None
    if kind == "regular":
        upper_bytes = _read_file_bytes(entry.upper_path)
    elif kind == "symlink":
        upper_bytes = os.readlink(entry.upper_path).encode("utf-8")
    else:
        upper_bytes = None
    return UpperChange(
        rel=entry.rel,
        kind=kind,
        base_bytes=base_bytes,
        upper_bytes=upper_bytes,
        base_existed=base_bytes is not None,
    )


def _entry_kind(entry: UpperEntry) -> UpperChangeKind:
    if is_whiteout(entry.st, entry.xattrs):
        return "whiteout"
    if stat.S_ISLNK(entry.st.st_mode):
        return "symlink"
    if is_opaque_dir(entry.st, entry.xattrs):
        return "opaque_dir"
    return "regular"


def _read_base_bytes(rel: str) -> bytes | None:
    path = _safe_lower_path(_NS_LOWER, rel)
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if os.path.islink(path):
        return os.readlink(path).encode("utf-8")
    if not os.path.isfile(path):
        return None
    return _read_file_bytes(path)


def _safe_lower_path(lower_root: str, rel: str) -> str:
    lower_root_abs = os.path.abspath(lower_root)
    if os.path.isabs(rel):
        raise RuntimeError(f"absolute overlay path is not allowed: {rel!r}")
    norm = os.path.normpath(rel.replace("\\", "/"))
    if norm in ("", ".") or norm.startswith("../"):
        raise RuntimeError(f"overlay path escapes lowerdir: {rel!r}")
    path = os.path.abspath(os.path.join(lower_root_abs, norm))
    if os.path.commonpath([lower_root_abs, path]) != lower_root_abs:
        raise RuntimeError(f"overlay path escapes lowerdir: {rel!r}")
    return path


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _read_xattrs(path: str) -> dict[bytes, bytes]:
    listxattr = getattr(os, "listxattr", None)
    getxattr = getattr(os, "getxattr", None)
    if listxattr is None or getxattr is None:
        return {}
    try:
        names = listxattr(path, follow_symlinks=False)
    except OSError:
        return {}
    out: dict[bytes, bytes] = {}
    for name in names:
        key = name.encode("utf-8") if isinstance(name, str) else name
        try:
            out[key] = getxattr(path, name, follow_symlinks=False)
        except OSError:
            continue
    return out


def is_whiteout(st: os.stat_result, xattrs: dict[bytes, bytes]) -> bool:
    if stat.S_ISCHR(st.st_mode) and st.st_rdev == 0:
        return True
    return stat.S_ISREG(st.st_mode) and st.st_size == 0 and (
        b"user.overlay.whiteout" in xattrs
    )


def is_opaque_dir(st: os.stat_result, xattrs: dict[bytes, bytes]) -> bool:
    if not stat.S_ISDIR(st.st_mode):
        return False
    return (
        xattrs.get(b"trusted.overlay.opaque") == b"y"
        or xattrs.get(b"user.overlay.opaque") == b"y"
    )


def reject_exit_code(reason: str) -> int:
    return _REJECT_EXIT_BASE + 7 if reason == REJECT_UPPER_FULL else _REJECT_EXIT_BASE


__all__ = [
    "REJECT_UPPER_FULL",
    "_parse_args",
    "_write_result_json",
    "is_opaque_dir",
    "is_whiteout",
    "main",
    "reject_exit_code",
    "run_user_command",
    "walk_upperdir",
]
