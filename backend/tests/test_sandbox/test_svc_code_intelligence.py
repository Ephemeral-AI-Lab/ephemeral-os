"""Contract tests for :class:`sandbox.api.code_intelligence_impl.SvcCodeIntelligence`.

The impl wraps a ``CodeIntelligenceService`` instance and exposes the
provider-neutral :class:`CodeIntelligenceApi` Protocol. Tests use a fake
svc whose methods return engine-shaped objects so the impl's translation
layer is exercised end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sandbox.api.code_intelligence_impl import SvcCodeIntelligence
from sandbox.api.models import (
    DiagnosticsRequest,
    ReferencesRequest,
    RequestActor,
    SymbolQueryRequest,
    WorkspaceStructureRequest,
)


@dataclass
class _EngineSymbol:
    """Minimal stand-in for engine ``SymbolInfo``."""
    name: str
    kind: str
    file_path: str
    line: int
    character: int = 0
    signature: str = ""
    container: str = ""


@dataclass
class _EngineReference:
    file_path: str
    line: int
    character: int = 0
    text: str = ""


@dataclass
class _EngineDiagnostic:
    line: int
    character: int = 0
    severity: str = "error"
    message: str = ""
    source: str = ""
    code: str = ""


@pytest.fixture
def actor() -> RequestActor:
    return RequestActor(agent_id="alice")


# -- status ------------------------------------------------------------------


async def test_status_translates_dict_to_workspace_status() -> None:
    svc = SimpleNamespace(
        status=MagicMock(
            return_value={
                "sandbox_id": "sb-1",
                "workspace_root": "/workspace",
                "initialized": True,
                "symbol_index": {"size": 42},
                "arbiter": {"locks": 0},
                "edit_buffer": {},
                "lsp": {"connected": True},
            }
        ),
    )
    api = SvcCodeIntelligence(svc)

    result = await api.status("sb-1")

    assert result.sandbox_id == "sb-1"
    assert result.workspace_root == "/workspace"
    assert result.initialized is True
    assert result.symbol_index == {"size": 42}
    assert result.lsp == {"connected": True}
    assert result.edit_hotspots is None


# -- query_symbols (name) ----------------------------------------------------


async def test_query_symbols_translates_definitions(actor: RequestActor) -> None:
    sample = [
        _EngineSymbol(name="Foo", kind="class", file_path="/x.py", line=10),
        _EngineSymbol(name="bar", kind="function", file_path="/y.py", line=5),
    ]
    svc = SimpleNamespace(query_symbols=MagicMock(return_value=sample))
    api = SvcCodeIntelligence(svc)

    result = await api.query_symbols(
        "sb-1", SymbolQueryRequest(query="Foo", actor=actor),
    )

    assert len(result.definitions) == 2
    assert result.definitions[0].name == "Foo"
    assert result.definitions[0].kind == "class"
    assert result.definitions[0].line == 10
    svc.query_symbols.assert_called_once_with("Foo")


async def test_query_symbols_filters_by_kind(actor: RequestActor) -> None:
    sample = [
        _EngineSymbol(name="Foo", kind="class", file_path="/x.py", line=10),
        _EngineSymbol(name="bar", kind="function", file_path="/y.py", line=5),
    ]
    svc = SimpleNamespace(query_symbols=MagicMock(return_value=sample))
    api = SvcCodeIntelligence(svc)

    result = await api.query_symbols(
        "sb-1", SymbolQueryRequest(query="*", actor=actor, kind="class"),
    )

    assert len(result.definitions) == 1
    assert result.definitions[0].kind == "class"


async def test_query_symbols_no_matches_yields_none_confidence(
    actor: RequestActor,
) -> None:
    svc = SimpleNamespace(query_symbols=MagicMock(return_value=[]))
    api = SvcCodeIntelligence(svc)

    result = await api.query_symbols(
        "sb-1", SymbolQueryRequest(query="missing", actor=actor),
    )

    assert result.definitions == ()
    assert result.confidence == "none"


# -- query_symbols (file path) -----------------------------------------------


async def test_query_symbols_with_file_path_uses_file_symbols(
    actor: RequestActor,
) -> None:
    sample = [
        _EngineSymbol(name="A", kind="class", file_path="/x.py", line=1),
        _EngineSymbol(name="b", kind="function", file_path="/x.py", line=20),
    ]
    si = SimpleNamespace(file_symbols=MagicMock(return_value=sample))
    svc = SimpleNamespace(symbol_index=si, query_symbols=MagicMock(return_value=[]))
    api = SvcCodeIntelligence(svc)

    result = await api.query_symbols(
        "sb-1", SymbolQueryRequest(query="src/x.py", actor=actor),
    )

    assert result.matched_file == "src/x.py"
    assert result.confidence == "file_symbols"
    assert len(result.definitions) == 2
    si.file_symbols.assert_called_once_with("src/x.py")


# -- find_references ---------------------------------------------------------


async def test_find_references_translates_engine_refs(actor: RequestActor) -> None:
    refs = [
        _EngineReference(file_path="/a.py", line=10, text="ref a"),
        _EngineReference(file_path="/b.py", line=5, text="ref b"),
    ]
    svc = SimpleNamespace(find_references=MagicMock(return_value=refs))
    api = SvcCodeIntelligence(svc)

    result = await api.find_references(
        "sb-1",
        ReferencesRequest(file_path="/x.py", symbol="Foo", actor=actor, line=12),
    )

    assert len(result.references) == 2
    assert result.references[0].file_path == "/a.py"
    assert result.references[0].text == "ref a"
    svc.find_references.assert_called_once_with("/x.py", "Foo", 12, 0)


# -- diagnostics -------------------------------------------------------------


async def test_diagnostics_returns_clean_when_empty(actor: RequestActor) -> None:
    svc = SimpleNamespace(diagnostics=MagicMock(return_value=[]))
    api = SvcCodeIntelligence(svc)

    result = await api.diagnostics(
        "sb-1", DiagnosticsRequest(file_path="/x.py", actor=actor),
    )

    assert result.diagnostics == ()
    assert result.clean is True


async def test_diagnostics_translates_engine_diagnostics(actor: RequestActor) -> None:
    raw = [
        _EngineDiagnostic(line=3, severity="warning", message="unused"),
        _EngineDiagnostic(line=5, severity="error", message="boom"),
    ]
    svc = SimpleNamespace(diagnostics=MagicMock(return_value=raw))
    api = SvcCodeIntelligence(svc)

    result = await api.diagnostics(
        "sb-1", DiagnosticsRequest(file_path="/x.py", actor=actor),
    )

    assert len(result.diagnostics) == 2
    assert result.diagnostics[0].severity == "warning"
    assert result.diagnostics[1].severity == "error"
    assert result.clean is False


# -- workspace_structure -----------------------------------------------------


async def test_workspace_structure_returns_indexed_paths(actor: RequestActor) -> None:
    si = SimpleNamespace(
        is_built=True,
        ensure_built=MagicMock(return_value=True),
        paths_with_prefix=MagicMock(return_value=[
            "/workspace/a.py",
            "/workspace/sub/b.py",
            "/workspace/sub/deep/c.py",
        ]),
    )
    svc = SimpleNamespace(symbol_index=si, workspace_root="/workspace")
    api = SvcCodeIntelligence(svc)

    result = await api.workspace_structure(
        "sb-1", WorkspaceStructureRequest(actor=actor, max_depth=3),
    )

    assert result.source == "index"
    assert result.workspace_root == "/workspace"
    # depth(a.py)=1, depth(sub/b.py)=2, depth(sub/deep/c.py)=3 — all ≤ 3.
    assert set(result.paths) == {"a.py", "sub/b.py", "sub/deep/c.py"}


async def test_workspace_structure_depth_filter_excludes_too_deep(
    actor: RequestActor,
) -> None:
    si = SimpleNamespace(
        is_built=True,
        paths_with_prefix=MagicMock(return_value=[
            "/workspace/a.py",
            "/workspace/sub/b.py",
            "/workspace/sub/deep/c.py",
        ]),
    )
    svc = SimpleNamespace(symbol_index=si, workspace_root="/workspace")
    api = SvcCodeIntelligence(svc)

    result = await api.workspace_structure(
        "sb-1", WorkspaceStructureRequest(actor=actor, max_depth=2),
    )

    # depth(a.py)=1, depth(sub/b.py)=2, depth(sub/deep/c.py)=3.
    # max_depth=2 keeps the first two and drops the deepest.
    assert "a.py" in result.paths
    assert "sub/b.py" in result.paths
    assert "sub/deep/c.py" not in result.paths


async def test_workspace_structure_returns_none_source_when_index_missing(
    actor: RequestActor,
) -> None:
    svc = SimpleNamespace(symbol_index=None, workspace_root="/workspace")
    api = SvcCodeIntelligence(svc)

    result = await api.workspace_structure(
        "sb-1", WorkspaceStructureRequest(actor=actor),
    )

    assert result.source == "none"
    assert result.paths == ()


# -- protocol method completeness --------------------------------------------


def test_svc_code_intelligence_satisfies_protocol_method_set() -> None:
    """``SvcCodeIntelligence`` declares every method named on the Protocol."""
    import inspect
    from sandbox.api.code_intelligence_api import CodeIntelligenceApi

    expected = {
        name
        for name, fn in inspect.getmembers(
            CodeIntelligenceApi, predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }
    declared = {
        name
        for name, fn in inspect.getmembers(
            SvcCodeIntelligence, predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }
    missing = expected - declared
    assert not missing, f"SvcCodeIntelligence missing methods: {sorted(missing)}"
