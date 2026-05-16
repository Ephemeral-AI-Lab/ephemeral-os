"""Tests for sandbox API payload helper contracts."""

from __future__ import annotations

import pytest

from sandbox.api.tool.core.daemon_response import (
    error_message,
    int_from_daemon_response,
)
from sandbox.api.tool.core.conflicts import (
    is_edit_conflict,
    is_shell_conflict,
)
from sandbox._shared.models import SandboxCaller


def test_sandbox_caller_audit_fields_keeps_required_keys_and_non_empty_fields() -> None:
    caller = SandboxCaller(
        agent_id="agent-1",
        task_center_run_id="tc-run",
        tool_id="tool-1",
    )

    assert caller.audit_fields() == {
        "agent_id": "agent-1",
        "run_id": "",
        "agent_run_id": "",
        "task_id": "",
        "task_center_run_id": "tc-run",
        "tool_id": "tool-1",
    }


def test_error_message_strips_internal_error_prefix() -> None:
    assert error_message(RuntimeError("internal_error: anchor not found")) == (
        "anchor not found"
    )


def test_int_from_daemon_response_is_strict_about_boundary_types() -> None:
    assert int_from_daemon_response(3, default=0) == 3
    assert int_from_daemon_response(None, default=7) == 7
    with pytest.raises(TypeError):
        int_from_daemon_response(True, default=0)
    with pytest.raises(TypeError):
        int_from_daemon_response("1", default=0)
    with pytest.raises(TypeError):
        int_from_daemon_response(1.5, default=0)


def test_conflict_detection_prefers_typed_error_codes() -> None:
    class CodedError(RuntimeError):
        code = "anchor_not_found"

    assert is_edit_conflict(CodedError("wording can change"))

    class DetailedError(RuntimeError):
        details = {"code": "unsupported_symlink_change"}

    assert is_shell_conflict(DetailedError("wording can change"))
