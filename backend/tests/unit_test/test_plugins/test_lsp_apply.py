"""Unit tests for LSP WorkspaceEdit application."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.catalog.lsp.runtime.apply import apply_workspace_edit


class _Overlay:
    def __init__(self, workspace_root: str) -> None:
        self.workspace_root = workspace_root
        self.published_paths: tuple[str, ...] = ()
        self.ensure_reasons: list[str] = []

    async def ensure_current(self, *, reason: str = "ensure_current") -> str:
        self.ensure_reasons.append(reason)
        return "hash@1"

    async def publish_workspace_paths(
        self,
        *,
        paths: tuple[str, ...],
        actor_id: str = "",
        description: str = "plugin workspace edit",
    ) -> object:
        del actor_id, description
        self.published_paths = paths
        return SimpleNamespace(
            success=True,
            published_manifest_version=2,
            files=(),
        )


@dataclass(frozen=True)
class _Caller:
    agent_id: str = "agent"


@dataclass(frozen=True)
class _Ctx:
    overlay: _Overlay
    caller: _Caller = _Caller()
    metadata: dict[str, str] | None = None


@pytest.mark.asyncio
async def test_apply_workspace_edit_writes_text_edits_and_publishes_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "testbed"
    module = workspace / "pkg" / "mod.py"
    module.parent.mkdir(parents=True)
    module.write_text("value = 1\nprint(value)\n", encoding="utf-8")
    overlay = _Overlay(workspace.as_posix())
    uri = module.as_uri()

    result = await apply_workspace_edit(
        {
            "changes": {
                uri: [
                    {
                        "range": {
                            "start": {"line": 0, "character": 8},
                            "end": {"line": 0, "character": 9},
                        },
                        "newText": "2",
                    }
                ]
            }
        },
        _Ctx(overlay=overlay),
    )

    assert module.read_text(encoding="utf-8") == "value = 2\nprint(value)\n"
    assert overlay.ensure_reasons == ["lsp:apply_workspace_edit:enter"]
    assert overlay.published_paths == ("pkg/mod.py",)
    assert result["success"] is True
    assert result["manifest_version"] == 2


@pytest.mark.asyncio
async def test_apply_workspace_edit_rejects_paths_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "testbed"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    overlay = _Overlay(workspace.as_posix())

    with pytest.raises(ValueError, match="outside workspace"):
        await apply_workspace_edit(
            {
                "changes": {
                    outside.as_uri(): [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 0},
                            },
                            "newText": "x = 1\n",
                        }
                    ]
                }
            },
            _Ctx(overlay=overlay),
        )

    assert not outside.exists()
    assert overlay.published_paths == ()


@pytest.mark.asyncio
async def test_apply_workspace_edit_handles_file_operations(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "testbed"
    old_path = workspace / "pkg" / "old.py"
    old_path.parent.mkdir(parents=True)
    old_path.write_text("x = 1\n", encoding="utf-8")
    overlay = _Overlay(workspace.as_posix())

    result = await apply_workspace_edit(
        {
            "documentChanges": [
                {"kind": "rename", "oldUri": old_path.as_uri(), "newUri": (workspace / "pkg" / "new.py").as_uri()},
                {"kind": "create", "uri": (workspace / "pkg" / "created.py").as_uri()},
                {"kind": "delete", "uri": (workspace / "pkg" / "created.py").as_uri()},
            ]
        },
        _Ctx(overlay=overlay),
    )

    assert not old_path.exists()
    assert (workspace / "pkg" / "new.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (workspace / "pkg" / "created.py").exists()
    assert overlay.published_paths == (
        "pkg/created.py",
        "pkg/new.py",
        "pkg/old.py",
    )
    assert result["changed_paths"] == ["pkg/created.py", "pkg/new.py", "pkg/old.py"]
