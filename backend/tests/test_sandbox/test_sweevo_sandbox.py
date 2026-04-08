"""Tests for SWE-EVO sandbox provisioning helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks.sweevo.models import SWEEvoInstance


def _instance() -> SWEEvoInstance:
    return SWEEvoInstance(
        instance_id="pydantic__pydantic_v2.6.0b1_v2.6.0",
        repo="pydantic/pydantic",
        base_commit="abc123",
        problem_statement="",
        patch="",
        fail_to_pass=[],
        pass_to_pass=[],
        docker_image="xingyaoww/sweb.eval.x86_64.pydantic_s_pydantic-8583",
        test_cmds="pytest",
        environment_setup_commit="",
    )


def test_default_sweevo_sandbox_name_is_unique():
    from benchmarks.sweevo.sandbox import _default_sweevo_sandbox_name

    instance = _instance()

    first = _default_sweevo_sandbox_name(instance)
    second = _default_sweevo_sandbox_name(instance)

    assert first != second
    assert first.startswith("sweevo-test-pydantic__pydantic")
    assert len(first) <= 63
    assert len(second) <= 63


@pytest.mark.asyncio
async def test_create_sweevo_test_sandbox_reuses_named_retry(monkeypatch):
    from benchmarks.sweevo import sandbox as sweevo_sandbox

    existing = {
        "id": "sb-existing",
        "name": "retry-sandbox",
        "labels": {"purpose": "sweevo-test"},
    }
    service = SimpleNamespace(
        list_sandboxes=lambda: [existing],
        create_sandbox=lambda **_: pytest.fail("should not create a new sandbox"),
    )
    setup_mock = AsyncMock()

    monkeypatch.setattr(sweevo_sandbox, "_service", lambda: service)
    monkeypatch.setattr(sweevo_sandbox, "setup_sweevo_sandbox", setup_mock)

    result = await sweevo_sandbox.create_sweevo_test_sandbox(
        _instance(),
        sandbox_name="retry-sandbox",
        register_snapshot=False,
    )

    assert result["sandbox_id"] == "sb-existing"
    assert result["sandbox"] == existing
    assert result["reused_existing"] is True
    setup_mock.assert_not_awaited()
