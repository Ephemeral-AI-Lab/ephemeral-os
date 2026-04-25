"""Unit tests for scope path utilities."""

from __future__ import annotations

from team.core.scope import (
    normalize_scope_paths,
    scope_paths_overlap,
)


def test_normalize_dedupes_and_sorts():
    result = normalize_scope_paths(["./src/auth/", "src/auth", "src/billing/"])
    assert result == ["src/auth", "src/billing"]


def test_overlaps_exact_match():
    assert scope_paths_overlap("src/auth", "src/auth") is True


def test_overlaps_parent_child():
    assert scope_paths_overlap("src/auth", "src/auth/session.py") is True


def test_overlaps_distinct_paths():
    assert scope_paths_overlap("src/auth", "src/billing") is False
