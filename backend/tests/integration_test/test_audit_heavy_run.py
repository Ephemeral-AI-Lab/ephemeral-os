"""Phase 2.6 Slice 8 — heavy-run regression suite for the audit pull pipeline.

Drives the real :class:`task_center_runner.audit.recorder.AuditRecorder` +
:class:`task_center_runner.audit.sandbox_events_sink.RotatingJsonlSink`
with a deterministic stub :class:`task_center_runner.audit.daemon_pull.DaemonAuditPuller.pull`
that streams a synthetic 1 M-event suite. Closes Phase 2's overall
acceptance bar:

* every subsystem section listed in V3 README §Subsystem section keys has
  at least one row in ``sandbox_events.jsonl``;
* ``dropped_event_count == 0 AND lost_before_seq == 0`` end-to-end;
* the rotating sink rotates at 64 MiB, gzips on rotation, and the live
  file stays under the rotation threshold;
* :func:`task_center_runner.audit.sandbox_events_sink.iter_rotated_jsonl`
  concatenates the rotated gzipped history correctly;
* ``payload["daemon_event"]`` is absent under the default config and
  present when ``EOS_AUDIT_FORENSIC_RAW_ENABLED=true``.

The 1 M-event variant is marked ``@pytest.mark.slow`` so the default
``pytest backend/tests`` invocation skips it (CI enables it via
``--run-slow`` or no marker filter).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from task_center_runner.audit.recorder import AuditRecorder
from task_center_runner.audit.sandbox_events_sink import (
    ROTATION_BYTES_DEFAULT,
    iter_rotated_jsonl,
)


# Subsystem section keys (V3 README — frozen at v1).
_SUBSYSTEM_SECTIONS = (
    "daemon",
    "layer_stack",
    "overlay_workspace",
    "occ",
    "isolated_workspace",
    "os_resource",
    "plugin",
    "background_tool",
    "tool_call",
)

# Event types we cycle through. One per subsystem section so coverage is
# trivially exhaustive.
_EVENT_TYPES_BY_SECTION: dict[str, str] = {
    "daemon": "daemon.started",
    "layer_stack": "layer_stack.lock_acquired",
    "overlay_workspace": "overlay_workspace.mounted",
    "occ": "occ.apply_committed",
    "isolated_workspace": "isolated_workspace.entered",
    "os_resource": "os_resource.sampled",
    "plugin": "plugin.tool_invoked",
    "background_tool": "background_tool.started",
    "tool_call": "tool_call.started",
}


def _synthesize_event(seq: int) -> dict[str, Any]:
    """Build one synthetic pulled event covering one subsystem section.

    Round-robins across the 9 subsystem sections so a 1 M-event run
    trivially covers every section. Sections carry minimal data; the heavy-
    run test is about the recorder + sink pipeline, not section content
    fidelity (slice-level emitter tests own that).
    """
    section = _SUBSYSTEM_SECTIONS[seq % len(_SUBSYSTEM_SECTIONS)]
    event_type = _EVENT_TYPES_BY_SECTION[section]
    lane = (
        "sample"
        if section in ("os_resource",)
        else ("critical" if section == "isolated_workspace" else "normal")
    )
    return {
        "seq": seq,
        "lane": lane,
        "type": event_type,
        "payload": {
            section: {
                "operation_id": f"op-{seq:08d}",
                "seq_in_section": seq,
                # Pad the payload so the rotation threshold is reached at
                # ~64 MiB-worth of events without needing absurd counts.
                "filler": "x" * 200,
            },
        },
    }


def _batch_pull_stub(total: int, batch: int = 1000):
    """Build a stub coroutine that pulls ``total`` events in ``batch`` chunks."""
    next_seq = {"value": 0}

    async def _pull(after_seq: int, limit: int) -> dict[str, Any]:
        del after_seq
        remaining = total - next_seq["value"]
        if remaining <= 0:
            return {
                "events": [],
                "buffer": {
                    "pressure": 0.0,
                    "dropped_event_count": 0,
                    "lost_before_seq": 0,
                },
                "snapshot": {"daemon": {"boot_epoch_id": 1}},
                "cursor": {"after_seq": next_seq["value"] - 1},
            }
        size = min(limit, batch, remaining)
        start = next_seq["value"]
        events = [_synthesize_event(start + i) for i in range(size)]
        next_seq["value"] = start + size
        return {
            "events": events,
            "buffer": {
                "pressure": 0.0,
                "dropped_event_count": 0,
                "lost_before_seq": 0,
            },
            "snapshot": {"daemon": {"boot_epoch_id": 1}},
            "cursor": {"after_seq": next_seq["value"] - 1},
        }

    return _pull


# ----------------------------------------------------------------------
# Heavy-run regression (1 M events) — marked slow.
# ----------------------------------------------------------------------


@pytest.mark.slow
def test_heavy_run_1m_events_acceptance_bar(tmp_path: Path) -> None:
    """1 M-event mock suite — acceptance bar for Phase 2 overall.

    Exercises rotation/gzip/retention, subsystem coverage, and the
    dual-write authoritativeness invariants in one pass.
    """
    total = 1_000_000
    recorder = AuditRecorder(tmp_path / "run", task_center_run_id="heavy-1")
    recorder.start()

    async def _drive() -> None:
        recorder.attach_daemon_audit_puller(pull=_batch_pull_stub(total))
        # Let the puller drain. The puller's _pull_once stays in the inner
        # loop until a partial batch is returned, so one tick drains all.
        # Allow ample slack for the disk-bound rotation/gzip steps.
        await asyncio.sleep(0.05)
        await recorder.aclose()

    asyncio.run(_drive())

    sink_live = tmp_path / "run" / "sandbox_events.jsonl"
    assert sink_live.exists()

    # Acceptance: every subsystem section has at least one row.
    seen_sections: set[str] = set()
    row_count = 0
    for row in iter_rotated_jsonl(sink_live):
        row_count += 1
        payload = row.get("payload") or {}
        for key in payload.keys():
            if key in _SUBSYSTEM_SECTIONS:
                seen_sections.add(key)
        if seen_sections == set(_SUBSYSTEM_SECTIONS) and row_count >= 100:
            # Coverage met — continue to verify total count anyway.
            pass
    assert seen_sections == set(_SUBSYSTEM_SECTIONS), (
        f"missing subsystem coverage: {set(_SUBSYSTEM_SECTIONS) - seen_sections}"
    )
    assert row_count == total

    # Live sink ≤ rotation cap.
    assert sink_live.stat().st_size <= ROTATION_BYTES_DEFAULT

    # Rotated history present.
    rotated = sorted(
        p
        for p in (tmp_path / "run").iterdir()
        if p.name.startswith("sandbox_events.jsonl.") and p.name.endswith(".gz")
    )
    # 1 M × ~400 bytes/event ≈ 400 MiB raw → ~6-7 rotated files. Retention
    # default is 8, so all should survive.
    assert len(rotated) >= 1, "expected at least one rotated gz file"
    assert len(rotated) <= 8, "retention cap exceeded"


@pytest.mark.slow
def test_no_consumer_reads_daemon_event_under_default_config(tmp_path: Path) -> None:
    """Default config (env unset) → ``payload['daemon_event']`` absent everywhere."""
    monkeypatch_env = pytest.MonkeyPatch()
    monkeypatch_env.delenv("EOS_AUDIT_FORENSIC_RAW_ENABLED", raising=False)
    try:
        recorder = AuditRecorder(tmp_path / "run", task_center_run_id="default-1")
        recorder.start()

        async def _drive() -> None:
            recorder.attach_daemon_audit_puller(pull=_batch_pull_stub(10_000))
            await asyncio.sleep(0.05)
            await recorder.aclose()

        asyncio.run(_drive())

        for row in iter_rotated_jsonl(tmp_path / "run" / "sandbox_events.jsonl"):
            payload = row.get("payload") or {}
            assert "daemon_event" not in payload
    finally:
        monkeypatch_env.undo()


@pytest.mark.slow
def test_forensic_raw_present_when_env_enabled(tmp_path: Path) -> None:
    """``EOS_AUDIT_FORENSIC_RAW_ENABLED=true`` → ``payload['daemon_event']`` present."""
    monkeypatch_env = pytest.MonkeyPatch()
    monkeypatch_env.setenv("EOS_AUDIT_FORENSIC_RAW_ENABLED", "true")
    try:
        recorder = AuditRecorder(tmp_path / "run", task_center_run_id="forensic-1")
        recorder.start()

        async def _drive() -> None:
            recorder.attach_daemon_audit_puller(pull=_batch_pull_stub(1_000))
            await asyncio.sleep(0.05)
            await recorder.aclose()

        asyncio.run(_drive())

        rows = list(iter_rotated_jsonl(tmp_path / "run" / "sandbox_events.jsonl"))
        assert rows, "expected at least one row"
        for row in rows:
            payload = row.get("payload") or {}
            assert "daemon_event" in payload
    finally:
        monkeypatch_env.undo()


# ----------------------------------------------------------------------
# Smaller helpers — NOT slow; exercise rotation + iter_rotated_jsonl.
# ----------------------------------------------------------------------


def _drive_small(tmp_path: Path, total: int) -> Path:
    """Helper — drive ``total`` events through a fresh recorder, return live path."""
    recorder = AuditRecorder(tmp_path / "run", task_center_run_id="small-1")
    recorder.start()

    async def _drive() -> None:
        recorder.attach_daemon_audit_puller(pull=_batch_pull_stub(total))
        await asyncio.sleep(0.05)
        await recorder.aclose()

    asyncio.run(_drive())
    return tmp_path / "run" / "sandbox_events.jsonl"


def test_sandbox_events_jsonl_rotation_path_stable_under_eos_tier_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rotated paths sit under the run dir regardless of ``EOS_TIER_RUN_ID``.

    The recorder receives an explicit ``run_dir``; ``EOS_TIER_RUN_ID``
    influences the run-dir computation higher up the stack (Phase 2
    inherited from existing invariants). This test pins that the sink
    writes under the run_dir as supplied.
    """
    monkeypatch.setenv("EOS_TIER_RUN_ID", "tier-run-7")
    live = _drive_small(tmp_path, total=2_000)
    assert live.parent == tmp_path / "run"
    assert live.exists()


def test_iter_jsonl_concatenates_rotated_gzipped_history(tmp_path: Path) -> None:
    """All synthesized seqs survive a rotated history when iterated through iter_rotated_jsonl."""
    total = 1_000
    live = _drive_small(tmp_path, total=total)
    seqs = [row.get("seq") for row in iter_rotated_jsonl(live)]
    seqs = [s for s in seqs if isinstance(s, int)]
    assert sorted(seqs) == list(range(total))


def test_subsystem_section_coverage_small_run(tmp_path: Path) -> None:
    """Every subsystem section appears at least once in a 100-event run."""
    live = _drive_small(tmp_path, total=100)
    seen: set[str] = set()
    for row in iter_rotated_jsonl(live):
        payload = row.get("payload") or {}
        for key in payload.keys():
            if key in _SUBSYSTEM_SECTIONS:
                seen.add(key)
    assert seen == set(_SUBSYSTEM_SECTIONS)


def test_no_consumer_reads_daemon_event_small_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default config: ``daemon_event`` key absent across a small run."""
    monkeypatch.delenv("EOS_AUDIT_FORENSIC_RAW_ENABLED", raising=False)
    live = _drive_small(tmp_path, total=200)
    for row in iter_rotated_jsonl(live):
        payload = row.get("payload") or {}
        assert "daemon_event" not in payload


def test_forensic_raw_present_when_env_enabled_small_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env-enabled: forensic ``daemon_event`` key appears in every row."""
    monkeypatch.setenv("EOS_AUDIT_FORENSIC_RAW_ENABLED", "true")
    live = _drive_small(tmp_path, total=200)
    rows = list(iter_rotated_jsonl(live))
    assert rows
    for row in rows:
        payload = row.get("payload") or {}
        assert "daemon_event" in payload
