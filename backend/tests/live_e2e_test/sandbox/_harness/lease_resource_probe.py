"""Command-exec telemetry harness for O(1) overlay-mount verification.

The O(1) native tests intentionally consume the same per-shell timing map that
live command execution emits. That keeps the probe on the production lease,
mount, capture, and resource-audit path instead of measuring a synthetic
``du``/``df`` side channel.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import statistics
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol
from unittest.mock import patch

from sandbox.daemon.service.layer_stack_client import LayerStackClient
from sandbox.execution.contract import CommandExecRequest, MountMode
from sandbox.execution.overlay.capability import new_mount_api_supported
from sandbox.execution.service import execute_command
from sandbox.execution.strategies.namespace import detect_private_mount_namespace

OverlayPath = Literal["new_mount_api", "legacy_materialize"]

NEW_MOUNT_API: OverlayPath = "new_mount_api"
LEGACY_MATERIALIZE: OverlayPath = "legacy_materialize"

LOWER_SIDE_LIMIT_BYTES = 4 * 1024
UPPERDIR_NO_WRITE_LIMIT_BYTES = 64 * 1024
LEGACY_NEGATIVE_CONTROL_RATIO = 100.0
MOUNT_SLOPE_LIMIT_S_PER_LAYER = 0.005
READ_CPU_SLOPE_LIMIT_S_PER_LAYER = 50.0 / 1_000_000.0
RSS_LIMIT_BYTES_PER_LEASE = 2 * 1024 * 1024


class LayerStackLike(Protocol):
    storage_root: Path

    def publish_changes(self, changes: Sequence[object]) -> object: ...


@dataclass(frozen=True)
class ShellTelemetry:
    request_id: str
    requested_path: OverlayPath
    mount_mode: str
    timings: dict[str, float]
    stdout: str
    stderr: str

    @property
    def lower_side_bytes(self) -> int:
        """Workspace bytes materialized below the command upperdir."""
        return int(self.timings.get("resource.command_exec.workspace_tree_bytes", 0.0))

    @property
    def upperdir_bytes(self) -> int:
        return int(self.timings.get("resource.command_exec.upperdir_tree_bytes", 0.0))

    @property
    def mount_workspace_s(self) -> float:
        return float(self.timings.get("command_exec.mount_workspace_s", 0.0))

    @property
    def cmd_user_s(self) -> float:
        return float(self.timings.get("cmd.exec.user_s", 0.0))

    @property
    def rss_bytes(self) -> int:
        return int(self.timings.get("resource.process.rss_bytes", 0.0))

    @property
    def manifest_depth(self) -> int:
        return int(self.timings.get("resource.layer_stack.manifest_depth", 0.0))


def has_cap_sys_admin() -> bool:
    """Return whether the native O(1) harness can exercise the new mount path."""
    if sys.platform != "linux":
        return False
    return detect_private_mount_namespace() and new_mount_api_supported()


def build_layer_stack(
    root: Path,
    *,
    manifest_depth: int,
    base_payload_bytes: int = 4096,
    per_layer_payload_bytes: int = 128,
) -> LayerStackLike:
    """Create a LayerStack with a bottom ``known_file.bin`` and M-1 overlays."""
    stack_cls = importlib.import_module("sandbox.layer_stack.stack").LayerStack
    write_layer_change = importlib.import_module(
        "sandbox.layer_stack.changes"
    ).WriteLayerChange
    root.mkdir(parents=True, exist_ok=True)
    source_root = root / "sources"
    source_root.mkdir(parents=True, exist_ok=True)
    stack = stack_cls(root / "stack")

    for index in range(manifest_depth):
        if index == 0:
            source = _write_payload(
                source_root / "known_file.bin",
                size=base_payload_bytes,
                byte=b"k",
            )
            path = "known_file.bin"
        else:
            source = _write_payload(
                source_root / f"layer-{index:04d}.txt",
                size=per_layer_payload_bytes,
                byte=bytes([97 + (index % 26)]),
            )
            path = f"layers/{index:04d}.txt"
        stack.publish_changes([write_layer_change(path=path, source_path=str(source))])

    return stack


def as_requested_path(
    telemetry: ShellTelemetry,
    requested_path: OverlayPath,
    *,
    request_id: str | None = None,
) -> ShellTelemetry:
    return replace(
        telemetry,
        requested_path=requested_path,
        request_id=request_id or telemetry.request_id,
    )


async def run_shell_batch(
    *,
    stack: LayerStackLike,
    workspace_root: Path,
    scratch_root: Path,
    requested_path: OverlayPath,
    commands: Sequence[str],
    request_prefix: str,
    timeout_seconds: float = 60.0,
) -> list[ShellTelemetry]:
    """Run commands through command-exec with the requested overlay path forced."""
    workspace_root.mkdir(parents=True, exist_ok=True)
    scratch_root.mkdir(parents=True, exist_ok=True)
    layer_client = LayerStackClient(stack)
    use_new_api = requested_path == NEW_MOUNT_API
    expected_mode = (
        MountMode.PRIVATE_NAMESPACE if use_new_api else MountMode.COPY_BACKED
    )

    async def _run_one(index: int, command: str) -> ShellTelemetry:
        request = CommandExecRequest(
            request_id=f"{request_prefix}-{index:04d}",
            workspace_ref=str(stack.storage_root),
            workspace_root=workspace_root.as_posix(),
            command=("bash", "-lc", command),
            timeout_seconds=timeout_seconds,
            description=f"o1 {requested_path}",
        )
        result = await execute_command(
            request,
            layer_stack=layer_client,
            occ_client=None,
            storage_root=stack.storage_root,
            occ_apply=False,
            mount_mode=expected_mode,
        )
        actual_mode = result.workspace_capture.mount_mode
        if actual_mode != expected_mode:
            raise AssertionError(
                f"{request.request_id} ran {actual_mode.value}; expected "
                f"{expected_mode.value} for {requested_path}"
            )
        if result.exit_code != 0:
            raise AssertionError(
                f"{request.request_id} exited {result.exit_code}: {result.stderr}"
            )
        return ShellTelemetry(
            request_id=request.request_id,
            requested_path=requested_path,
            mount_mode=actual_mode.value,
            timings=dict(result.timings),
            stdout=result.stdout,
            stderr=result.stderr,
        )

    with _command_exec_scratch_root(scratch_root), patch(
        "sandbox.execution.service.new_mount_api_supported",
        return_value=use_new_api,
    ):
        return list(await asyncio.gather(*(_run_one(i, cmd) for i, cmd in enumerate(commands))))


def assert_bound_a_negative_control(
    *,
    new_api: Sequence[ShellTelemetry],
    legacy: Sequence[ShellTelemetry],
) -> None:
    """Assert Bound A with the legacy materialization negative control."""
    assert_new_api_o1_bounds(new_api)
    max_new_lower = max((row.lower_side_bytes for row in new_api), default=0)
    min_legacy_lower = min((row.lower_side_bytes for row in legacy), default=0)
    denominator = max(1, max_new_lower)
    ratio = min_legacy_lower / denominator
    if ratio < LEGACY_NEGATIVE_CONTROL_RATIO:
        raise AssertionError(
            "Bound A negative control FAIL: "
            f"min legacy lower-side bytes={min_legacy_lower}, "
            f"max new-api lower-side bytes={max_new_lower}, ratio={ratio:.1f}x "
            f"< {LEGACY_NEGATIVE_CONTROL_RATIO:.0f}x"
        )


def assert_new_api_o1_bounds(rows: Sequence[ShellTelemetry]) -> None:
    """Assert new API lower-side O(1) and no unexpected upper writes.

    Collect every offending lease before raising so adversarial self-tests can
    prove multiple regressions are named in one failure message.
    """
    failures: list[str] = []
    for row in rows:
        if row.lower_side_bytes > LOWER_SIDE_LIMIT_BYTES:
            failures.append(
                f"{row.request_id}: forced materialize/lower-side bytes "
                f"{row.lower_side_bytes} > {LOWER_SIDE_LIMIT_BYTES}"
            )
        if row.upperdir_bytes > UPPERDIR_NO_WRITE_LIMIT_BYTES:
            failures.append(
                f"{row.request_id}: upper write bytes "
                f"{row.upperdir_bytes} > {UPPERDIR_NO_WRITE_LIMIT_BYTES}"
            )
    if failures:
        raise AssertionError("O(1) lease bound regression(s): " + "; ".join(failures))


def assert_mount_slope_by_depth(
    rows_by_depth: dict[int, Sequence[ShellTelemetry]],
) -> None:
    medians = {
        depth: statistics.median(row.mount_workspace_s for row in rows)
        for depth, rows in sorted(rows_by_depth.items())
        if rows
    }
    _assert_slope(
        medians,
        limit=MOUNT_SLOPE_LIMIT_S_PER_LAYER,
        label="mount_workspace_s",
    )
    lower_side_by_depth = {
        depth: max(row.lower_side_bytes for row in rows)
        for depth, rows in rows_by_depth.items()
        if rows
    }
    offenders = {
        depth: value
        for depth, value in lower_side_by_depth.items()
        if value > LOWER_SIDE_LIMIT_BYTES
    }
    if offenders:
        raise AssertionError(
            "Bound B lower-side disk is not flat in M: "
            + ", ".join(f"M={depth} lower={value}" for depth, value in offenders.items())
        )


def assert_read_cpu_slope_by_depth(
    rows_by_depth: dict[int, Sequence[ShellTelemetry]],
) -> None:
    medians = {
        depth: statistics.median(row.cmd_user_s for row in rows)
        for depth, rows in sorted(rows_by_depth.items())
        if rows
    }
    _assert_slope(
        medians,
        limit=READ_CPU_SLOPE_LIMIT_S_PER_LAYER,
        label="cmd.exec.user_s",
    )


def assert_memory_bound(*, n1: Sequence[ShellTelemetry], n200: Sequence[ShellTelemetry]) -> None:
    rss_at_1 = max((row.rss_bytes for row in n1), default=0)
    rss_at_200 = max((row.rss_bytes for row in n200), default=0)
    delta_per_lease = max(0, rss_at_200 - rss_at_1) / 200.0
    if delta_per_lease > RSS_LIMIT_BYTES_PER_LEASE:
        raise AssertionError(
            "Memory bound FAIL: "
            f"(rss_at_N200={rss_at_200} - rss_at_N1={rss_at_1}) / 200 "
            f"= {delta_per_lease:.0f} bytes > {RSS_LIMIT_BYTES_PER_LEASE}"
        )


def fail_if_depth_errors(errors: dict[int, BaseException], *, label: str) -> None:
    if not errors:
        return
    details = "; ".join(f"M={depth}: {exc!r}" for depth, exc in sorted(errors.items()))
    raise AssertionError(f"{label} failed at one or more depths: {details}")


def _write_payload(path: Path, *, size: int, byte: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(byte * size)
    return path


def _assert_slope(
    values_by_depth: dict[int, float],
    *,
    limit: float,
    label: str,
) -> None:
    if len(values_by_depth) < 2:
        return
    depths = sorted(values_by_depth)
    for left, right in zip(depths, depths[1:]):
        slope = (values_by_depth[right] - values_by_depth[left]) / (right - left)
        if slope > limit:
            raise AssertionError(
                f"{label} slope FAIL between M={left} and M={right}: "
                f"{slope:.9f}s/layer > {limit:.9f}s/layer "
                f"({values_by_depth[left]:.6f}s -> {values_by_depth[right]:.6f}s)"
            )


@contextmanager
def _command_exec_scratch_root(path: Path) -> Iterator[None]:
    previous = os.environ.get("EPHEMERALOS_COMMAND_EXEC_SCRATCH_ROOT")
    os.environ["EPHEMERALOS_COMMAND_EXEC_SCRATCH_ROOT"] = path.as_posix()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("EPHEMERALOS_COMMAND_EXEC_SCRATCH_ROOT", None)
        else:
            os.environ["EPHEMERALOS_COMMAND_EXEC_SCRATCH_ROOT"] = previous
