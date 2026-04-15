"""Unit tests for ScopePath utilities."""

from __future__ import annotations

from team._path_utils import ScopePath, scope_paths_from_payload


def test_scope_path_normalize_dedupes_and_sorts():
    result = ScopePath.normalize(["./src/auth/", "src/auth", "src/billing/"])
    assert result == ["src/auth", "src/billing"]


def test_scope_path_overlaps_exact_match():
    assert ScopePath.overlaps("src/auth", "src/auth") is True


def test_scope_path_overlaps_parent_child():
    assert ScopePath.overlaps("src/auth", "src/auth/session.py") is True


def test_scope_path_overlaps_distinct_paths():
    assert ScopePath.overlaps("src/auth", "src/billing") is False


def test_scope_path_from_payload_extracts_paths():
    payload = {
        "paths": ["src/auth/session.py"],
        "verify": "pytest tests/test_auth.py::test_login",
    }
    assert scope_paths_from_payload(payload) == ["src/auth/session.py", "tests/test_auth.py"]
