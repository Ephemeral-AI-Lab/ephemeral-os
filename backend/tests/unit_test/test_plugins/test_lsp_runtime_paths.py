"""Unit tests for plugins.catalog.lsp.runtime.paths."""

from __future__ import annotations

import pytest

from plugins.catalog.lsp.runtime.paths import (
    PathMapper,
    PathMappingError,
    repo_path_from_snapshot_uri,
    snapshot_uri_from_repo_path,
)


def test_to_snapshot_uri_repo_relative() -> None:
    mapper = PathMapper(lowerdir="/tmp/snap")
    assert (
        mapper.to_snapshot_uri("pkg/mod.py") == "file:///tmp/snap/pkg/mod.py"
    )


def test_to_snapshot_uri_workspace_absolute_strips_root() -> None:
    mapper = PathMapper(lowerdir="/tmp/snap", workspace_root="/testbed")
    assert (
        mapper.to_snapshot_uri("/testbed/pkg/mod.py")
        == "file:///tmp/snap/pkg/mod.py"
    )


def test_round_trip_repo_path() -> None:
    mapper = PathMapper(lowerdir="/tmp/snap")
    uri = mapper.to_snapshot_uri("pkg/sub/mod.py")
    assert mapper.from_snapshot_uri(uri) == "pkg/sub/mod.py"


def test_uri_with_quoted_chars_round_trips() -> None:
    uri = snapshot_uri_from_repo_path("/tmp/snap", "dir with spaces/m.py")
    assert "%20" in uri or " " in uri  # quoted form
    assert (
        repo_path_from_snapshot_uri("/tmp/snap", uri)
        == "dir with spaces/m.py"
    )


def test_uri_outside_lowerdir_rejected() -> None:
    with pytest.raises(PathMappingError, match="not under projection lowerdir"):
        repo_path_from_snapshot_uri(
            "/tmp/snap", "file:///etc/passwd"
        )


def test_empty_path_rejected() -> None:
    mapper = PathMapper(lowerdir="/tmp/snap")
    with pytest.raises(PathMappingError, match="empty file_path"):
        mapper.to_snapshot_uri("")


def test_lowerdir_required() -> None:
    with pytest.raises(PathMappingError, match="lowerdir is required"):
        snapshot_uri_from_repo_path("", "pkg/mod.py")
