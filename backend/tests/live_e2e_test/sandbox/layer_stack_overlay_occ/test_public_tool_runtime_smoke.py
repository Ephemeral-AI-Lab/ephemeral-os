"""Integrated live smoke for public sandbox file tools."""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


pytestmark = pytest.mark.asyncio


async def test_public_tools_commit_through_sandbox_runtime(
    integrated_sandbox: SandboxHandle,
) -> None:
    path = "smoke/public_tools.txt"

    write = await integrated_sandbox.tool.write_file(
        path,
        "alpha\n",
        description="live integrated write smoke",
    )
    assert write.success, write.conflict_reason
    assert write.status == "committed"
    assert path in write.changed_paths

    edit = await integrated_sandbox.tool.edit_file(
        path,
        [("alpha", "beta")],
        description="live integrated edit smoke",
    )
    assert edit.success, edit.conflict_reason
    assert edit.status == "committed"
    assert edit.applied_edits == 1

    shell = await integrated_sandbox.tool.shell(
        "set -e; "
        "grep -q beta smoke/public_tools.txt; "
        "printf 'from shell\\n' > smoke/shell.txt; "
        "printf 'seen:%s' \"$(cat smoke/public_tools.txt)\"",
        timeout=30,
        description="live integrated shell smoke",
    )
    assert shell.success, shell.stderr or shell.conflict_reason
    assert shell.exit_code == 0
    assert "seen:beta" in shell.stdout
    assert "smoke/shell.txt" in shell.changed_paths

    edited = await integrated_sandbox.tool.read_file(path)
    assert edited.success
    assert edited.exists
    assert edited.content == "beta\n"

    shell_output = await integrated_sandbox.tool.read_file("smoke/shell.txt")
    assert shell_output.success
    assert shell_output.exists
    assert shell_output.content == "from shell\n"
