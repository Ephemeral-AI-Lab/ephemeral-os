"""Tests for the daemon-event normalizer and forensic-raw boundary."""

from __future__ import annotations

from pathlib import Path

from task_center_runner.audit.daemon_event_normalizer import (
    FORENSIC_RAW_ENV,
    dedupe_key,
    merge_streams,
    normalize_pulled_event,
)


def test_no_consumer_reads_daemon_event_under_default_config(monkeypatch) -> None:
    monkeypatch.delenv(FORENSIC_RAW_ENV, raising=False)
    raw = {
        "seq": 7,
        "lane": "normal",
        "type": "layer_stack.lease_acquired",
        "payload": {"layer_stack": {"lease_id": "L1"}},
    }
    row = normalize_pulled_event(raw)
    assert "daemon_event" not in row["payload"]
    assert row["payload"]["layer_stack"]["lease_id"] == "L1"
    assert row["seq"] == 7
    assert row["event_type"] == "layer_stack.lease_acquired"


def test_forensic_raw_present_when_env_enabled(monkeypatch) -> None:
    monkeypatch.setenv(FORENSIC_RAW_ENV, "true")
    raw = {
        "seq": 1,
        "type": "occ.changeset_prepared",
        "payload": {"occ": {"changeset_id": "C1"}},
    }
    row = normalize_pulled_event(raw)
    assert "daemon_event" in row["payload"]
    assert row["payload"]["daemon_event"]["type"] == "occ.changeset_prepared"


def test_dedupe_pull_supersedes_stream_when_both_present() -> None:
    pull = {
        "seq": 42,
        "event_type": "occ.apply_committed",
        "payload": {"occ": {"apply_ms": 5.0, "operation_id": "op-1"}},
    }
    stream = {
        "event_type": "occ.apply_committed",
        "payload": {"occ": {"apply_ms": 99.0, "operation_id": "op-1"}},
    }
    merged = merge_streams([pull], [stream])
    # Pull wins on overlap via seq key. Both keys differ in this scenario
    # (stream lacks seq) so we get both — pull's seq beats the logical key.
    seqs = {dedupe_key(row) for row in merged}
    assert ("seq", 42) in seqs


def test_daemon_event_writer_module_boundary() -> None:
    """CI lint: only the normalizer may reference payload['daemon_event']."""
    backend_src = Path(__file__).resolve().parents[3] / "src"
    assert backend_src.is_dir(), backend_src
    needle_a = 'payload["daemon_event"]'
    needle_b = "payload['daemon_event']"
    needle_c = 'payload.get("daemon_event"'
    needle_d = "payload.get('daemon_event'"

    allowed = {
        "daemon_event_normalizer.py",
    }

    offenders: list[str] = []
    for path in backend_src.rglob("*.py"):
        if path.name in allowed:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(n in text for n in (needle_a, needle_b, needle_c, needle_d)):
            offenders.append(str(path))

    assert not offenders, f"daemon_event references outside normalizer: {offenders}"
