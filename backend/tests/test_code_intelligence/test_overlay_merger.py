"""Unit tests for :mod:`code_intelligence.routing.overlay_merger`."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest

from code_intelligence.routing.overlay_merger import (
    GitMergeFileMerger,
    OverwriteMerger,
)


@pytest.mark.asyncio
async def test_overwrite_merger_returns_upperdir_verbatim() -> None:
    result = await OverwriteMerger().merge(
        sandbox=object(),
        path="/repo/a.py",
        lowerdir="/lower",
        repo_root="/repo",
        upperdir_content="x = 1\n",
    )
    assert result.merged_content == "x = 1\n"
    assert result.conflicts == 0
    assert result.strategy == "overwrite"


@pytest.mark.asyncio
async def test_git_merger_clean_merge() -> None:
    """Simulate ``git merge-file`` reporting exit 0 and returning merged
    content via the read-back step."""
    merged_text = "line1\nline2\nmerged\n"
    calls: list[str] = []

    async def fake_exec(sandbox: Any, command: str, *, timeout: Any) -> Any:
        calls.append(command)
        if command.startswith("mkdir -p"):
            return SimpleNamespace(result="")
        if "git merge-file" in command:
            return SimpleNamespace(result="0")
        if "base64 <" in command:
            return SimpleNamespace(
                result=base64.b64encode(merged_text.encode("utf-8")).decode(
                    "ascii"
                )
            )
        return SimpleNamespace(result="")

    merger = GitMergeFileMerger(exec_process=fake_exec)
    result = await merger.merge(
        sandbox=object(),
        path="/repo/pkg/a.py",
        lowerdir="/lower",
        repo_root="/repo",
        upperdir_content="ignored\n",
    )
    assert result.merged_content == merged_text
    assert result.conflicts == 0
    assert result.strategy == "git_merge_file"


@pytest.mark.asyncio
async def test_git_merger_reports_conflicts() -> None:
    async def fake_exec(sandbox: Any, command: str, *, timeout: Any) -> Any:
        if "git merge-file" in command:
            return SimpleNamespace(result="2")  # 2 conflict hunks
        if "base64 <" in command:
            return SimpleNamespace(
                result=base64.b64encode(b"<<<< conflict marker >>>>\n").decode(
                    "ascii"
                )
            )
        return SimpleNamespace(result="")

    merger = GitMergeFileMerger(exec_process=fake_exec)
    result = await merger.merge(
        sandbox=object(),
        path="/repo/a.py",
        lowerdir="/lower",
        repo_root="/repo",
        upperdir_content="ignored",
    )
    assert result.conflicts == 2
    assert "conflict marker" in result.merged_content


@pytest.mark.asyncio
async def test_git_merger_falls_back_to_overwrite_on_stage_failure() -> None:
    async def fake_exec(sandbox: Any, command: str, *, timeout: Any) -> Any:
        if command.startswith("mkdir -p"):
            raise RuntimeError("sandbox disconnected")
        return SimpleNamespace(result="")

    merger = GitMergeFileMerger(exec_process=fake_exec)
    result = await merger.merge(
        sandbox=object(),
        path="/repo/a.py",
        lowerdir="/lower",
        repo_root="/repo",
        upperdir_content="fallback-content\n",
    )
    assert result.merged_content == "fallback-content\n"
    assert result.strategy == "overwrite"
