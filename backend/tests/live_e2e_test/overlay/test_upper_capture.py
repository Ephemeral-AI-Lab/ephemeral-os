"""Overlay upperdir capture round-trip.

Backs §4.2. Pass bar: capture matches ``OverlayCapture`` schema; ordering
preserved across 1k captures.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from sandbox.overlay.capture.types import OverlayCapture

from .._harness.assertions import assert_no_torn_reads
from .._harness.sandbox_fixture import SandboxHandle
from .._harness.workload import commit_layer


def _print_metrics(label: str, payload: dict) -> None:
    print(f"\n[{label}] {json.dumps(payload, separators=(',', ':'))}")


async def _run_overlay_shell(
    handle: SandboxHandle, command: tuple[str, ...]
) -> OverlayCapture:
    return await handle.overlay_client.shell(
        command,
        request_id=uuid.uuid4().hex,
        cwd=".",
        env={},
        timeout_seconds=30,
    )


@pytest.mark.asyncio
async def test_upperdir_captures_writes_deletes_and_whiteouts(
    overlay_sandbox: SandboxHandle, tmp_path,
) -> None:
    """OverlayClient.shell returns a typed OverlayCapture with all change kinds."""
    payloads = tmp_path / "upper_payloads"
    commit_layer(
        overlay_sandbox.layer_stack, payloads, "base",
        body="seed-content\n", layer_path="seed/keep.txt",
    )
    commit_layer(
        overlay_sandbox.layer_stack, payloads, "victim",
        body="bye\n", layer_path="seed/victim.txt",
    )
    # Mutate inside the snapshot: write a new file, delete an existing one.
    capture = await _run_overlay_shell(
        overlay_sandbox,
        (
            "/bin/sh", "-c",
            "set -e; mkdir -p new; printf 'hello' > new/welcome.txt; "
            "rm seed/victim.txt",
        ),
    )
    assert capture.exit_code == 0, capture
    payload = capture.to_dict()
    _print_metrics(
        "E4.upper_capture.changes",
        {"changes": payload["changes"], "snapshot_version": capture.snapshot_version},
    )
    # Schema round-trip: OverlayCapture <-> dict
    round_trip = OverlayCapture.from_dict(payload).to_dict()
    assert round_trip == payload, "OverlayCapture round-trip mismatch"
    kinds = {change["kind"] for change in payload["changes"]}
    paths = {change["path"] for change in payload["changes"]}
    assert "write" in kinds, payload["changes"]
    assert "delete" in kinds, payload["changes"]
    assert "new/welcome.txt" in paths
    assert "seed/victim.txt" in paths


@pytest.mark.asyncio
async def test_capture_serializes_to_diff_ndjson_in_order(
    overlay_sandbox: SandboxHandle, tmp_path,
) -> None:
    """Repeated captures preserve change ordering across 1k iterations."""
    iterations = int(
        os.environ.get("EPHEMERALOS_OVERLAY_CAPTURE_ITERATIONS", "1000")
    )
    payloads = tmp_path / "ordering_payloads"
    commit_layer(
        overlay_sandbox.layer_stack, payloads, "ord-base",
        body="seed", layer_path="ord/seed.txt",
    )

    captures: list[OverlayCapture] = []
    t0 = time.perf_counter()
    for i in range(iterations):
        captures.append(
            await _run_overlay_shell(
                overlay_sandbox,
                (
                    "/bin/sh", "-c",
                    f"printf '%s' '{i}' > ord/iter_{i:05d}.txt",
                ),
            )
        )
    elapsed_s = time.perf_counter() - t0

    # Every capture must have a deterministic shape.
    bad = [c for c in captures if c.exit_code != 0 or len(c.changes) != 1]
    assert not bad, f"{len(bad)} bad captures (first={bad[0]!r})"

    # Ordering: the i-th capture must reference iter_{i:05d}.txt.
    for i, capture in enumerate(captures):
        change = capture.changes[0]
        assert change.path == f"ord/iter_{i:05d}.txt", (
            f"capture {i} out of order: {change.path}"
        )

    # Convert to diff-ndjson and verify ordering is preserved.
    ndjson_lines = [
        json.dumps(c.to_dict()["changes"][0], separators=(",", ":"))
        for c in captures
    ]
    for i, line in enumerate(ndjson_lines):
        assert json.loads(line)["path"] == f"ord/iter_{i:05d}.txt"

    # Torn-read invariant: every captured write must declare a final_hash;
    # repeat paths (none here, but if they appeared) must agree on hash.
    assert_no_torn_reads(c.to_dict()["changes"][0] for c in captures)

    summary = {
        "iterations": iterations,
        "elapsed_s": round(elapsed_s, 3),
        "per_call_ms_mean": round(elapsed_s * 1000.0 / iterations, 3),
        "snapshot_versions_min": min(c.snapshot_version for c in captures),
        "snapshot_versions_max": max(c.snapshot_version for c in captures),
    }
    _print_metrics("E4.capture_ordering.summary", summary)


@pytest.mark.asyncio
async def test_symlink_kind_round_trips(
    overlay_sandbox: SandboxHandle, tmp_path,
) -> None:
    """`ln -s` produces a `symlink` change with a non-empty `final_hash`."""
    payloads = tmp_path / "sym_payloads"
    commit_layer(
        overlay_sandbox.layer_stack, payloads, "sym-base",
        body="target-body", layer_path="sym/target.txt",
    )
    capture = await _run_overlay_shell(
        overlay_sandbox,
        ("/bin/sh", "-c", "ln -s target.txt sym/link.txt"),
    )
    assert capture.exit_code == 0, capture
    payload = capture.to_dict()
    _print_metrics("E4.symlink_kind.changes", {"changes": payload["changes"]})

    by_kind: dict[str, dict] = {}
    for change in payload["changes"]:
        by_kind.setdefault(change["kind"], change)
    assert "symlink" in by_kind, (
        f"expected a symlink change, got kinds={sorted(by_kind)}; "
        f"raw={payload['changes']}"
    )
    sym = by_kind["symlink"]
    assert sym["path"] == "sym/link.txt"
    assert sym["final_hash"], "symlink must declare final_hash for round-trip"
    assert sym["content_path"], "symlink must point at an upperdir entry"

    # Schema round-trip survives the symlink kind too.
    assert OverlayCapture.from_dict(payload).to_dict() == payload
