"""Unit tests for code intelligence service lifecycle and edits."""

from __future__ import annotations

import concurrent.futures
import threading
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
from code_intelligence.types import EditRequest, ReferenceInfo
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


def test_scope_status_filters_recent_history_by_team_run_id(tmp_path) -> None:
    scoped_file = tmp_path / "scoped.py"
    external_file = tmp_path / "external.py"

    svc = CodeIntelligenceService(
        sandbox_id="sandbox-scope-status",
        workspace_root=str(tmp_path),
    )

    svc.arbiter.record_edit(
        str(scoped_file),
        team_run_id="team-a",
        agent_run_id="run-a",
        task_id="task-a",
        edit_type="edit",
    )
    svc.arbiter.record_edit(
        str(external_file),
        team_run_id="team-b",
        agent_run_id="run-b",
        task_id="task-b",
        edit_type="edit",
    )

    packet = svc.scope_status([str(tmp_path)], team_run_id="team-a")

    assert packet["recent_changes"] == [
        {
            "file_path": str(scoped_file),
            "agent_run_id": "run-a",
            "task_id": "task-a",
            "timestamp": packet["recent_changes"][0]["timestamp"],
            "edit_type": "edit",
        }
    ]
    assert packet["hotspots"] == [{"file_path": str(scoped_file), "edit_count": 1}]


def test_get_code_intelligence_recreates_service_when_workspace_root_changes() -> None:
    first = get_code_intelligence("sandbox-reinit", "/tmp/first")
    second = get_code_intelligence("sandbox-reinit", "/tmp/second")

    assert second is not first
    assert get_code_intelligence_if_exists("sandbox-reinit") is second
    assert second.workspace_root == "/tmp/second"
    assert second.symbol_index._workspace_root == "/tmp/second"
    assert second.lsp_client._workspace_root == "/tmp/second"


def test_get_code_intelligence_rebind_resets_lsp_backend_cache_for_new_sandbox() -> None:
    first_sandbox = SimpleNamespace(name="first")
    second_sandbox = SimpleNamespace(name="second")

    service = get_code_intelligence("sandbox-rebind", "/tmp/workspace", sandbox=first_sandbox)
    service.lsp_client._py_available = False
    service.lsp_client._ts_available = False

    rebound = get_code_intelligence("sandbox-rebind", "/tmp/workspace", sandbox=second_sandbox)

    assert rebound is service
    assert rebound.symbol_index._sandbox is second_sandbox
    assert rebound.lsp_client._sandbox is second_sandbox
    assert rebound.lsp_client._py_available is None
    assert rebound.lsp_client._ts_available is None


def test_ensure_initialized_bootstraps_missing_lsp_once(tmp_path) -> None:
    svc = CodeIntelligenceService(
        sandbox_id="sandbox-bootstrap",
        workspace_root=str(tmp_path),
        sandbox=SimpleNamespace(),
    )

    calls: list[bool] = []

    def fake_ensure_ready(*, install_missing: bool = False, languages=None):
        calls.append(install_missing)
        if install_missing:
            return {"python": True, "typescript": True}
        return {"python": False, "typescript": False}

    svc.lsp_client.ensure_ready = fake_ensure_ready  # type: ignore[method-assign]

    svc.ensure_initialized(wait=False)
    svc.ensure_initialized(wait=False)

    assert calls[:2] == [False, True]
    assert calls.count(True) == 1


def test_rename_symbol_plan_uses_fast_reference_replacements(tmp_path) -> None:
    core = tmp_path / "core.py"
    use = tmp_path / "use.py"
    core.write_text("def beta(value):\n    return value\n", encoding="utf-8")
    use.write_text("from core import beta\nresult = beta(1)\n", encoding="utf-8")
    svc = CodeIntelligenceService(
        sandbox_id="sandbox-preview-rename",
        workspace_root=str(tmp_path),
    )
    svc.lsp_client.find_references = MagicMock(  # type: ignore[method-assign]
        return_value=[
            ReferenceInfo(file_path=str(core), line=1, character=4),
            ReferenceInfo(file_path=str(use), line=1, character=17),
            ReferenceInfo(file_path=str(use), line=2, character=9),
        ]
    )
    svc.lsp_client.rename_symbol = MagicMock(return_value={})  # type: ignore[method-assign]

    plan = svc.rename_symbol_plan(str(core), 1, 0, "gamma")

    assert len(plan.changes) == 2
    final_by_path = {change.file_path: change.final_content for change in plan.changes}
    assert final_by_path[str(core)] == "def gamma(value):\n    return value\n"
    assert final_by_path[str(use)] == "from core import gamma\nresult = gamma(1)\n"
    svc.lsp_client.rename_symbol.assert_not_called()


def test_rename_symbol_plan_falls_back_when_reference_span_is_unverified(
    tmp_path,
) -> None:
    core = tmp_path / "core.py"
    core.write_text("def beta(value):\n    return value\n", encoding="utf-8")
    svc = CodeIntelligenceService(
        sandbox_id="sandbox-preview-rename-fallback",
        workspace_root=str(tmp_path),
    )
    svc.lsp_client.find_references = MagicMock(  # type: ignore[method-assign]
        return_value=[ReferenceInfo(file_path=str(core), line=1, character=0)]
    )
    svc.lsp_client.rename_symbol = MagicMock(  # type: ignore[method-assign]
        return_value={str(core): "def gamma(value):\n    return value\n"}
    )

    plan = svc.rename_symbol_plan(str(core), 1, 0, "gamma")

    assert len(plan.changes) == 1
    assert plan.changes[0].final_content == "def gamma(value):\n    return value\n"
    svc.lsp_client.rename_symbol.assert_called_once_with(str(core), 1, 0, "gamma")


def test_rename_symbol_plan_singleflights_snapshot_reads(tmp_path) -> None:
    core = tmp_path / "core.py"
    use = tmp_path / "use.py"
    core.write_text("def beta(value):\n    return value\n", encoding="utf-8")
    use.write_text("from core import beta\nresult = beta(1)\n", encoding="utf-8")
    svc = CodeIntelligenceService(
        sandbox_id="sandbox-preview-rename-singleflight",
        workspace_root=str(tmp_path),
    )
    svc.lsp_client.find_references = MagicMock(  # type: ignore[method-assign]
        return_value=[
            ReferenceInfo(file_path=str(core), line=1, character=4),
            ReferenceInfo(file_path=str(use), line=1, character=17),
            ReferenceInfo(file_path=str(use), line=2, character=9),
        ]
    )
    original_read_many = svc._content.read_many

    def slow_read_many(paths, *, allow_missing: bool = False):
        time.sleep(0.05)
        return original_read_many(paths, allow_missing=allow_missing)

    svc._content.read_many = MagicMock(side_effect=slow_read_many)  # type: ignore[method-assign]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        plans = list(
            executor.map(
                lambda idx: svc.rename_symbol_plan(
                    str(core),
                    1,
                    0,
                    f"gamma_{idx}",
                ),
                range(4),
            )
        )

    assert [plan.new_name for plan in plans] == ["gamma_0", "gamma_1", "gamma_2", "gamma_3"]
    assert all(len(plan.changes) == 2 for plan in plans)
    assert svc.lsp_client.find_references.call_count == 1
    assert svc._content.read_many.call_count == 1


def test_is_initialized_tracks_background_build_completion() -> None:
    svc = CodeIntelligenceService(
        sandbox_id="sandbox-background-init",
        workspace_root="/tmp/nonexistent-background-init",
    )
    build_event = svc.symbol_index._build_event

    def fake_ensure_built(*, wait: bool = True, timeout: float = 30.0) -> bool:
        def complete_build() -> None:
            with svc.symbol_index._lock:
                svc.symbol_index._built = True
                svc.symbol_index._building = False
                build_event.set()

        timer = threading.Timer(0.01, complete_build)
        timer.start()
        return False

    svc.symbol_index.ensure_built = fake_ensure_built  # type: ignore[method-assign]

    svc.ensure_initialized(wait=False)

    assert build_event.wait(timeout=1.0) is True
    assert svc.symbol_index.is_built is True
    assert svc.is_initialized is True
    assert svc.status()["initialized"] is True


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


def test_symbol_index_builds_from_remote_sandbox_workspace() -> None:
    def list_files(path: str):
        entries = {
            "/repo": [
                SimpleNamespace(name="pkg", is_dir=True),
                SimpleNamespace(name="README.md", is_dir=False),
            ],
            "/repo/pkg": [
                SimpleNamespace(name="module.py", is_dir=False),
                SimpleNamespace(name="ignore.bin", is_dir=False),
            ],
        }
        return entries.get(path, [])

    def download_file(path: str):
        contents = {
            "/repo/README.md": b"# Remote repo\n",
            "/repo/pkg/module.py": b"class Remote:\n    def run(self):\n        return 1\n",
        }
        return contents[path]

    sandbox = SimpleNamespace(
        fs=SimpleNamespace(list_files=list_files, download_file=download_file),
    )
    idx = SymbolIndex("/repo", sandbox=sandbox)

    ready = idx.ensure_built(wait=True, timeout=1.0)

    assert ready is True
    assert idx.is_built is True
    assert idx.indexed_files == 2
    names = {symbol.name for symbol in idx.file_symbols("/repo/pkg/module.py")}
    rel_names = {symbol.name for symbol in idx.file_symbols("pkg/module.py")}
    assert "Remote" in names
    assert "Remote.run" in names
    assert rel_names == names


def test_symbol_index_returns_symbol_boundaries_for_python_symbols(tmp_path) -> None:
    file_path = tmp_path / "sample.py"
    content = (
        "class Example:\n"
        "    def method(self):\n"
        "        return 1\n"
    )
    file_path.write_text(content, encoding="utf-8")
    idx = SymbolIndex(str(tmp_path))

    idx.refresh(str(file_path), content)
    boundaries = idx.symbol_boundaries_for_file(str(file_path))

    assert ("Example", 1, 3) in boundaries
    assert ("Example.method", 2, 3) in boundaries
