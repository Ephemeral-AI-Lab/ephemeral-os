"""CLI orchestration for the sandbox-side overlay runtime."""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time

from .capture import build_upper_change, walk_upperdir
from .command import run_user_command
from .mounts import _NS_UPPER, OverlayMountError, setup_mounts
from .ndjson import write_diff_ndjson, write_result_json


def parse_args(argv: list[str]) -> argparse.Namespace:
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


def record_timing(timings: dict[str, float], key: str, started_at: float) -> None:
    timings[key] = round(time.perf_counter() - started_at, 6)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - e2e path
    total_started = time.perf_counter()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    workspace_root = args.workspace_root.rstrip("/")
    run_dir = args.run_dir.rstrip("/")
    os.makedirs(run_dir, exist_ok=True)

    run_timings: dict[str, float] = {}

    try:
        setup_started = time.perf_counter()
        setup_mounts(live_root=workspace_root, upper_size_mb=args.upper_size_mb)
        record_timing(run_timings, "setup_mounts", setup_started)
    except OverlayMountError as exc:
        print(str(exc), file=sys.stderr)
        return 255

    decode_started = time.perf_counter()
    user_cmd = base64.b64decode(args.user_cmd_b64).decode("utf-8")
    stdin_bytes = base64.b64decode(args.stdin_b64) if args.stdin_b64 else None
    record_timing(run_timings, "decode_command", decode_started)

    user_started = time.perf_counter()
    stdout_path = os.path.join(run_dir, "stdout.bin")
    _stdout_bytes, exit_code = run_user_command(
        user_cmd=user_cmd,
        stdin_bytes=stdin_bytes,
        cwd=workspace_root,
        stdout_path=stdout_path,
    )
    record_timing(run_timings, "user_command", user_started)

    walk_started = time.perf_counter()
    upper_entries = list(walk_upperdir(_NS_UPPER))
    upper_changes = tuple(build_upper_change(entry) for entry in upper_entries)
    upper_bytes = sum(len(change.upper_bytes or b"") for change in upper_changes)
    record_timing(run_timings, "walk_upperdir", walk_started)
    run_timings["total"] = round(time.perf_counter() - total_started, 6)

    write_diff_ndjson(
        run_dir=run_dir,
        exit_code=exit_code,
        upper_changes=upper_changes,
        upper_bytes=upper_bytes,
        upper_files=len(upper_changes),
        run_timings=run_timings,
    )
    write_result_json(
        run_dir=run_dir,
        exit_code=exit_code,
        rejected=None,
        run_timings=run_timings,
    )
    return exit_code


__all__ = [
    "main",
    "parse_args",
    "record_timing",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
