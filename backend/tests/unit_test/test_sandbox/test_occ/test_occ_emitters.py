"""Phase 2.5 slice 3 — ``occ.*`` daemon-ring emitter coverage.

Tests target the central ``_emit_occ_commit_events`` helper in
``sandbox.occ.service`` so we can drive deterministic inputs without
spinning up the full OccService + CommitQueue stack.
"""

from __future__ import annotations

from typing import Any

import pytest

from sandbox.daemon.audit_buffer import get_audit_buffer
from sandbox.occ.changeset import (
    ChangesetResult,
    FileResult,
    FileStatus,
    PreparedChangeset,
)
from sandbox.occ.service import _emit_occ_commit_events


_AUDIT_CURSOR = {"seq": -1}


class _FakeManifest:
    def __init__(self, version: int = 5) -> None:
        self.version = version


def _drain_occ_events() -> list[dict[str, Any]]:
    buf = get_audit_buffer()
    snap = buf.pull(after_seq=_AUDIT_CURSOR["seq"], limit=10_000)
    events = snap.get("events", [])
    if events:
        _AUDIT_CURSOR["seq"] = int(events[-1]["seq"])
    return [evt for evt in events if str(evt.get("type", "")).startswith("occ.")]


@pytest.fixture(autouse=True)
def _reset_audit_cursor() -> None:
    buf = get_audit_buffer()
    cursor = -1
    while True:
        snap = buf.pull(after_seq=cursor, limit=10_000)
        events = snap.get("events", [])
        if not events:
            break
        cursor = int(events[-1]["seq"])
    _AUDIT_CURSOR["seq"] = cursor
    yield


def _prepared(version: int = 5, changeset_id: str = "cs_abc1234567890fa") -> PreparedChangeset:
    return PreparedChangeset(
        snapshot=_FakeManifest(version),  # type: ignore[arg-type]
        path_groups=(),
        atomic=True,
        changeset_id=changeset_id,
    )


def test_occ_apply_committed_lane_is_normal_publish_layer_present() -> None:
    result = ChangesetResult(
        files=(FileResult(path="src/foo.py", status=FileStatus.COMMITTED),),
        timings={},
        published_manifest_version=12,
    )
    _emit_occ_commit_events(result, prepared=_prepared(), commit_elapsed=0.01)
    events = _drain_occ_events()
    types_by_lane = {evt["type"]: evt["lane"] for evt in events}
    assert types_by_lane["occ.apply_committed"] == "normal"
    assert types_by_lane["occ.publish_layer"] == "normal"
    apply = next(e for e in events if e["type"] == "occ.apply_committed")
    assert apply["payload"]["occ"]["base_manifest_version"] == 5
    assert apply["payload"]["occ"]["current_manifest_version"] == 12
    assert apply["payload"]["occ"]["changed_path_count"] == 1


def test_occ_conflict_rejected_carries_both_manifest_versions_and_critical_lane() -> None:
    result = ChangesetResult(
        files=(
            FileResult(
                path="src/foo.py",
                status=FileStatus.ABORTED_VERSION,
                message="base hash drifted",
            ),
        ),
        timings={},
        published_manifest_version=12,
    )
    _emit_occ_commit_events(result, prepared=_prepared(), commit_elapsed=0.0)
    events = _drain_occ_events()
    assert [e["type"] for e in events] == ["occ.conflict_rejected"]
    conflict = events[0]
    assert conflict["lane"] == "critical"
    section = conflict["payload"]["occ"]
    assert section["conflict_kind"] == "aborted_version"
    assert section["conflict_path"] == "src/foo.py"
    assert section["base_manifest_version"] == 5
    assert section["current_manifest_version"] == 12


def test_occ_apply_committed_carries_changeset_id() -> None:
    """Closer A: every OCC apply emit MUST carry the prepared changeset_id."""
    result = ChangesetResult(
        files=(FileResult(path="x", status=FileStatus.COMMITTED),),
        timings={},
        published_manifest_version=3,
    )
    _emit_occ_commit_events(
        result, prepared=_prepared(changeset_id="cs_apply_id_aaaa"), commit_elapsed=0.0
    )
    events = _drain_occ_events()
    apply = next(e for e in events if e["type"] == "occ.apply_committed")
    publish = next(e for e in events if e["type"] == "occ.publish_layer")
    assert apply["payload"]["occ"]["changeset_id"] == "cs_apply_id_aaaa"
    assert publish["payload"]["occ"]["changeset_id"] == "cs_apply_id_aaaa"


def test_occ_conflict_rejected_carries_changeset_id() -> None:
    """Closer A: conflict emits MUST also carry the prepared changeset_id."""
    result = ChangesetResult(
        files=(
            FileResult(
                path="x",
                status=FileStatus.ABORTED_OVERLAP,
                message="base hash drifted",
            ),
        ),
        timings={},
        published_manifest_version=4,
    )
    _emit_occ_commit_events(
        result, prepared=_prepared(changeset_id="cs_conflict_id_b"), commit_elapsed=0.0
    )
    events = _drain_occ_events()
    conflict = next(e for e in events if e["type"] == "occ.conflict_rejected")
    assert conflict["payload"]["occ"]["changeset_id"] == "cs_conflict_id_b"


def test_prepared_changeset_id_is_stable_across_replay() -> None:
    """Closer A: same inputs MUST produce the same changeset_id across replays."""
    from sandbox.occ.changeset import (
        ChangeSource,
        PreparedPathGroup,
        RouteDecision,
        WriteChange,
        WritePayload,
        compute_changeset_id,
    )

    payload = WritePayload(content=b"hello world\n")
    change = WriteChange(
        path="src/foo.py", source=ChangeSource.API_WRITE, payload=payload
    )
    group = PreparedPathGroup(
        path="src/foo.py",
        route=RouteDecision.GATED,
        changes=(change,),
    )
    id_a = compute_changeset_id(
        snapshot=_FakeManifest(7), path_groups=(group,), atomic=True
    )
    id_b = compute_changeset_id(
        snapshot=_FakeManifest(7), path_groups=(group,), atomic=True
    )
    assert id_a == id_b
    assert len(id_a) == 16

    # Distinct inputs MUST hash to a different id (collision domain check).
    change_alt = WriteChange(
        path="src/foo.py",
        source=ChangeSource.API_WRITE,
        payload=WritePayload(content=b"DIFFERENT\n"),
    )
    group_alt = PreparedPathGroup(
        path="src/foo.py",
        route=RouteDecision.GATED,
        changes=(change_alt,),
    )
    id_c = compute_changeset_id(
        snapshot=_FakeManifest(7), path_groups=(group_alt,), atomic=True
    )
    assert id_a != id_c
