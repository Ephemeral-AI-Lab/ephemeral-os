"""Dual-side pin assert: vendored fixture hash == pinned upstream hash."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_FIXTURES_PKG = (
    Path(__file__).resolve().parents[3] / "src" / "sandbox" / "_contract_fixtures"
)


def _compute_vendored_sha256(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "pin.json"):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def test_vendored_fixtures_match_pinned_hash() -> None:
    pin = json.loads((_FIXTURES_PKG / "pin.json").read_text())
    assert pin["upstream_commit"]
    expected = pin["fixtures_sha256"]
    actual = _compute_vendored_sha256(_FIXTURES_PKG)
    assert actual == expected, (
        "Vendored eos-protocol fixtures drifted from the pinned hash. "
        "Re-vendor + re-pin (CONTRACT.md), or revert the fixture edit."
    )
