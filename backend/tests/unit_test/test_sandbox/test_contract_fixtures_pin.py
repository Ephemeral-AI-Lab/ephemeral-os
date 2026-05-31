"""Dual-CI pin assert: vendored fixture hash == pinned upstream hash.

Phase 0: skips while the upstream fixture set is UNPINNED. Once
eos-protocol/fixtures is frozen and the backend vendors + pins a copy, this
flips to a hard assert so any vendored/upstream drift fails Python CI.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

_FIXTURES_PKG = (
    Path(__file__).resolve().parents[3] / "src" / "sandbox" / "_contract_fixtures"
)


def _compute_vendored_sha256(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*.json") if p.name != "pin.json"):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def test_vendored_fixtures_match_pinned_hash() -> None:
    pin = json.loads((_FIXTURES_PKG / "pin.json").read_text())
    if pin.get("upstream_commit") in ("", "UNPINNED"):
        pytest.skip("eos-protocol fixtures not frozen/pinned yet (Phase 0 scaffold)")
    expected = pin["fixtures_sha256"]
    actual = _compute_vendored_sha256(_FIXTURES_PKG)
    assert actual == expected, (
        "Vendored eos-protocol fixtures drifted from the pinned hash. "
        "Re-vendor + re-pin (CONTRACT.md), or revert the fixture edit."
    )
