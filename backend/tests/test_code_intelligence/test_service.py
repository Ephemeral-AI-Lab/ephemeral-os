"""Unit tests for code intelligence service lifecycle and edits."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from code_intelligence.analysis.symbol_index import SymbolIndex
from code_intelligence.routing.service import (
    CodeIntelligenceService,
    dispose_all_code_intelligence,
    get_code_intelligence,
    get_code_intelligence_if_exists,
)
from code_intelligence.types import EditRequest
from server.routers.code_intelligence import initialize


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    dispose_all_code_intelligence()
    yield
    dispose_all_code_intelligence()


def test_apply_edit_sandbox_upload_uses_content_then_path(tmp_path) -> None:
    file_path = tmp_path / "sample.py"
    file_path.write_text("value = 1\n", encoding="utf-8")

    sandbox = SimpleNamespace(fs=MagicMock())
    sandbox.fs.download_file.return_value = b"value = 1\n"
    svc = CodeIntelligenceService(
        sandbox_id="sandbox-edit",
        workspace_root=str(tmp_path),
        sandbox=sandbox,
    )

    result = svc.apply_edit(
        EditRequest(
            file_path=str(file_path),
            old_text="value = 1",
            new_text="value = 2",
            description="bump value",
        )
    )

    assert result.success is True
    sandbox.fs.upload_file.assert_called_once_with(
        b"value = 2\n",
        str(file_path),
    )


def test_get_code_intelligence_recreates_service_when_workspace_root_changes() -> None:
    first = get_code_intelligence("sandbox-reinit", "/tmp/first")
    second = get_code_intelligence("sandbox-reinit", "/tmp/second")

    assert second is not first
    assert get_code_intelligence_if_exists("sandbox-reinit") is second
    assert second.workspace_root == "/tmp/second"
    assert second.symbol_index._workspace_root == "/tmp/second"
    assert second.lsp_client._workspace_root == "/tmp/second"


def test_service_exposes_atlas_component() -> None:
    svc = CodeIntelligenceService(
        sandbox_id="sandbox-atlas",
        workspace_root="/tmp/atlas",
    )

    assert svc.atlas.ledger is svc.ledger
    assert svc.atlas.symbol_index is svc.symbol_index
    assert svc.atlas.workspace_root == "/tmp/atlas"
    assert "atlas" in svc.status()


@pytest.mark.asyncio
async def test_initialize_endpoint_passes_requested_workspace_root(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    fake_service = SimpleNamespace(
        workspace_root="",
        ensure_initialized=lambda wait=True: True,
    )

    def fake_get_code_intelligence(
        sandbox_id: str,
        workspace_root: str = "/workspace",
        sandbox=None,
    ):
        calls.append((sandbox_id, workspace_root))
        return fake_service

    monkeypatch.setattr(
        "code_intelligence.routing.service.get_code_intelligence",
        fake_get_code_intelligence,
    )

    result = await initialize("sandbox-init", workspace_root="/tmp/project")

    assert result == {"sandbox_id": "sandbox-init", "initialized": True}
    assert calls == [("sandbox-init", "/tmp/project")]


def test_symbol_index_missing_root_unblocks_waiters() -> None:
    idx = SymbolIndex("/tmp/definitely-missing-symbol-index-root")

    ready = idx.ensure_built(wait=True, timeout=0.05)
    time.sleep(0.01)

    assert ready is False
    assert idx._building is False
    assert idx._build_event.is_set() is True
