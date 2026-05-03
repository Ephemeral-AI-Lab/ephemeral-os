"""Tests for OCC daemon wire-format helpers."""

from __future__ import annotations

import pytest

from sandbox.occ.types import EditSpec
from sandbox.occ.wire import editspec_from_dict, editspec_to_dict


def test_editspec_from_dict_rejects_line_range_edit() -> None:
    with pytest.raises(ValueError, match="unsupported edit kind: line_range"):
        editspec_from_dict(
            {
                "file_path": "/ws/a.py",
                "edits": [
                    {
                        "kind": "line_range",
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "replacement\n",
                    }
                ],
            }
        )


def test_editspec_from_dict_rejects_implicit_line_range_edit() -> None:
    with pytest.raises(ValueError, match="unsupported edit kind: line_range"):
        editspec_from_dict(
            {
                "file_path": "/ws/a.py",
                "edits": [
                    {
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "replacement\n",
                    }
                ],
            }
        )


def test_editspec_from_dict_requires_search_text() -> None:
    with pytest.raises(ValueError, match="edit requires old_text"):
        editspec_from_dict(
            {
                "file_path": "/ws/a.py",
                "edits": [{"new_text": "replacement\n"}],
            }
        )


def test_editspec_from_dict_requires_replacement_text() -> None:
    with pytest.raises(ValueError, match="edit requires new_text"):
        editspec_from_dict(
            {
                "file_path": "/ws/a.py",
                "edits": [{"old_text": "target"}],
            }
        )


def test_editspec_to_dict_rejects_line_range_edit() -> None:
    with pytest.raises(ValueError, match="unsupported edit kind: line_range"):
        editspec_to_dict(
            EditSpec(
                file_path="/ws/a.py",
                edits=(
                    {
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "replacement\n",
                    },
                ),
            )
        )


def test_editspec_to_dict_rejects_unknown_edit_kind() -> None:
    with pytest.raises(ValueError, match="unsupported edit kind: insert"):
        editspec_to_dict(
            EditSpec(
                file_path="/ws/a.py",
                edits=({"kind": "insert", "old_text": "a", "new_text": "b"},),
            )
        )
