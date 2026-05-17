"""Tests for :func:`benchmarks.sweevo.sandbox.verify_sweevo_snapshot_exists`.

The verifier is a fail-fast probe the CSV benchmarker calls before any
Daytona sandbox is created. It must surface missing snapshots, non-active
states, and Daytona SDK enum-repr drift (per MEMORY.md R5).
"""

from __future__ import annotations

import pytest

import sandbox.api as sandbox_api
from benchmarks.sweevo.dataset import default_sweevo_snapshot_name
from benchmarks.sweevo.models import SWEEvoInstance
from benchmarks.sweevo.sandbox import (
    SnapshotNotRegisteredError,
    verify_sweevo_snapshot_exists,
)


def _instance(instance_id: str = "dask__dask_2023.3.2_2023.4.0") -> SWEEvoInstance:
    return SWEEvoInstance(
        instance_id=instance_id,
        repo="dask/dask",
        base_commit="abc",
        problem_statement="",
        patch="",
        fail_to_pass=[],
        pass_to_pass=[],
        docker_image="sweevo/dask:abc",
        test_cmds="pytest",
        environment_setup_commit="",
    )


def test_verify_returns_name_when_active(monkeypatch: pytest.MonkeyPatch) -> None:
    inst = _instance()
    expected = default_sweevo_snapshot_name(inst)
    monkeypatch.setattr(
        sandbox_api,
        "list_snapshots",
        lambda: [{"name": expected, "state": "active"}, {"name": "other", "state": "active"}],
    )

    assert verify_sweevo_snapshot_exists(inst) == expected


def test_verify_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    inst = _instance()
    expected = default_sweevo_snapshot_name(inst)
    monkeypatch.setattr(
        sandbox_api,
        "list_snapshots",
        lambda: [{"name": "unrelated", "state": "active"}],
    )

    with pytest.raises(SnapshotNotRegisteredError) as exc_info:
        verify_sweevo_snapshot_exists(inst)

    message = str(exc_info.value)
    assert expected in message
    assert inst.instance_id in message
    assert "register_sweevo_snapshot" in message


def test_verify_raises_when_state_error(monkeypatch: pytest.MonkeyPatch) -> None:
    inst = _instance()
    expected = default_sweevo_snapshot_name(inst)
    monkeypatch.setattr(
        sandbox_api,
        "list_snapshots",
        lambda: [{"name": expected, "state": "error"}],
    )

    with pytest.raises(SnapshotNotRegisteredError) as exc_info:
        verify_sweevo_snapshot_exists(inst)

    assert "error" in str(exc_info.value)
    assert expected in str(exc_info.value)


def test_verify_raises_when_state_building(monkeypatch: pytest.MonkeyPatch) -> None:
    inst = _instance()
    expected = default_sweevo_snapshot_name(inst)
    monkeypatch.setattr(
        sandbox_api,
        "list_snapshots",
        lambda: [{"name": expected, "state": "building"}],
    )

    with pytest.raises(SnapshotNotRegisteredError) as exc_info:
        verify_sweevo_snapshot_exists(inst)

    assert "building" in str(exc_info.value)


def test_verify_normalizes_enum_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per R5, accept ``'SnapshotState.ACTIVE'`` shaped state strings.

    Daytona SDK enum reprs round-trip through ``str(...)`` as
    ``'<EnumName>.<MEMBER>'``; the verifier must strip the prefix and
    lowercase before comparing.
    """
    inst = _instance()
    expected = default_sweevo_snapshot_name(inst)
    monkeypatch.setattr(
        sandbox_api,
        "list_snapshots",
        lambda: [{"name": expected, "state": "SnapshotState.ACTIVE"}],
    )

    assert verify_sweevo_snapshot_exists(inst) == expected


def test_verify_treats_unknown_state_as_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``state`` key resolves to ``'unknown'`` → SnapshotNotRegisteredError."""
    inst = _instance()
    expected = default_sweevo_snapshot_name(inst)
    monkeypatch.setattr(
        sandbox_api,
        "list_snapshots",
        lambda: [{"name": expected}],  # no state key
    )

    with pytest.raises(SnapshotNotRegisteredError) as exc_info:
        verify_sweevo_snapshot_exists(inst)

    assert "unknown" in str(exc_info.value)
