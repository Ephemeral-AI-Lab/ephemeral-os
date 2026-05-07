"""Phase 05 — OCC mutation gate runtime-boundary + retry-bound tests.

These tests assert the §6 structural invariants:

* occ-server is not a host-callable runtime dispatch module.
* Public data operations dispatch through ``runtime.handlers``.
* In-workspace classifier predicate lives in command-exec only;
  occ-server source contains no ``workspace_root`` classification call
  sites.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from sandbox.runtime import occ_server


# ---------------------------------------------------------------------------
# Runtime boundary
# ---------------------------------------------------------------------------


def test_legacy_occ_handlers_module_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("sandbox.runtime.occ_handlers")


def test_occ_server_module_does_not_classify_paths() -> None:
    """occ-server must not own the in-workspace classifier — single source of
    truth lives on command-exec (handlers/_common.py)."""
    occ_server_source = Path(occ_server.__file__).read_text()

    assert ".workspace_root" not in occ_server_source
    assert "workspace_root =" not in occ_server_source
    assert "workspace_root==" not in occ_server_source


def test_data_api_ops_do_not_dispatch_to_occ_server() -> None:
    """Data API ops must never route directly to occ-server."""
    from sandbox.runtime import server

    server._load_peer_bootstraps()
    for op in ("api.write_file", "api.edit_file", "api.read_file", "api.shell"):
        handler = server.OP_TABLE[op]
        assert handler.__module__.startswith("sandbox.runtime.handlers.")


# ---------------------------------------------------------------------------
# CAS retry exhaustion bound (MAX_OCC_CAS_RETRIES default 3)
# ---------------------------------------------------------------------------


def test_max_occ_cas_retries_is_named_constant_with_positive_default() -> None:
    """MAX_OCC_CAS_RETRIES is the public, testable retry budget."""
    from sandbox.occ.serial_merger import MAX_OCC_CAS_RETRIES

    assert isinstance(MAX_OCC_CAS_RETRIES, int)
    assert MAX_OCC_CAS_RETRIES >= 1
    # Plan §1 says default = 3.
    assert MAX_OCC_CAS_RETRIES == 3


@pytest.mark.asyncio
async def test_cas_retry_loop_bounded_under_no_contention(tmp_path: Path) -> None:
    """A no-contention write completes promptly — regression guard against the
    retry loop turning into a busy spin."""
    import asyncio

    from sandbox.layer_stack.workspace_base import build_workspace_base
    from sandbox.runtime import occ_server
    from sandbox.runtime.handlers import write_handler

    occ_server._backend_cache_clear()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)

    result = await asyncio.wait_for(
        write_handler.write_file(
            {
                "layer_stack_root": stack.as_posix(),
                "path": "ok.txt",
                "content": "fine\n",
            }
        ),
        timeout=2.0,
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_cas_retry_exhaustion_returns_conflict_result(tmp_path: Path) -> None:
    """Persistent CAS mismatch surfaces a per-path conflict result and does
    NOT loop indefinitely. We monkey-patch the layer-stack publisher to
    always raise :class:`ManifestConflictError` so every retry attempt fails."""
    import asyncio

    from sandbox.layer_stack.manifest import ManifestConflictError
    from sandbox.layer_stack.workspace_base import build_workspace_base
    from sandbox.occ.serial_merger import MAX_OCC_CAS_RETRIES
    from sandbox.runtime import occ_server
    from sandbox.runtime.handlers import write_handler
    from sandbox.runtime.handlers._common import _services

    occ_server._backend_cache_clear()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)

    services = _services(stack.as_posix())
    publisher = services.manager._publisher  # type: ignore[attr-defined]

    call_counter = {"n": 0}
    real_publish = publisher.publish_layer_locked

    def always_cas_mismatch(*_args, **_kwargs):
        call_counter["n"] += 1
        raise ManifestConflictError(
            "synthetic CAS mismatch for retry-exhaustion test"
        )

    publisher.publish_layer_locked = always_cas_mismatch  # type: ignore[method-assign]
    try:
        result = await asyncio.wait_for(
            write_handler.write_file(
                {
                    "layer_stack_root": stack.as_posix(),
                    "path": "ok.txt",
                    "content": "should-fail\n",
                }
            ),
            timeout=3.0,
        )
    finally:
        publisher.publish_layer_locked = real_publish  # type: ignore[method-assign]

    # Result is a conflict, not an exception; success is False.
    assert result["success"] is False
    assert result["conflict"] is not None
    # The conflict path carries ABORTED_VERSION semantics.
    assert "CAS mismatch retry budget exhausted" in result["conflict"]["message"]
    # Retry budget was respected — exactly MAX retries observed.
    assert call_counter["n"] == MAX_OCC_CAS_RETRIES


# ---------------------------------------------------------------------------
# Phase 05.5 — single OCC backend per layer_stack_root across all peers
# ---------------------------------------------------------------------------


def test_single_occ_backend_cache_per_layer_stack_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All runtime peers share one OccBackend per layer_stack_root.

    After Phase 05.5 the OCC backend tuple is owned by ``occ_server``;
    the per-verb handler scaffolding (write/edit/read/shell) and the
    api-handler manager helper all resolve through the same factory.
    """
    from sandbox.runtime import command_exec_server, occ_server
    from sandbox.runtime.handlers import _common

    occ_server._backend_cache_clear()

    class _FakeManager:
        def __init__(self, root: str) -> None:
            self.root = root

        @property
        def storage_root(self) -> str:
            return f"{self.root}/storage"

    class _FakeLayerStack:
        def __init__(self, manager: _FakeManager) -> None:
            self.manager = manager

        @property
        def storage_root(self) -> str:
            return self.manager.storage_root

    monkeypatch.setattr(
        occ_server,
        "get_layer_stack_manager",
        lambda root: _FakeManager(str(root)),
    )
    monkeypatch.setattr(occ_server, "LayerStackClient", _FakeLayerStack)
    monkeypatch.setattr(
        occ_server,
        "SnapshotGitignoreOracle",
        lambda layer_stack, **kwargs: ("oracle", layer_stack, kwargs),
    )
    monkeypatch.setattr(
        occ_server,
        "OccService",
        lambda *, gitignore, layer_stack: ("service", gitignore, layer_stack),
    )
    monkeypatch.setattr(
        occ_server,
        "OCCClient",
        lambda service, *, binding_reader, workspace_ref: (
            "occ-client",
            service,
            workspace_ref,
        ),
    )

    backend_a = occ_server.build_occ_backend("/tmp/a")

    # The per-verb scaffolding resolves to the cached OccBackend instance.
    via_common = _common._services("/tmp/a")
    assert via_common is backend_a
    assert occ_server.build_occ_backend("/tmp/a/.") is backend_a

    # command_exec_server returns a 4-tuple; the first three fields
    # identity-match the cached OccBackend's fields.
    via_command_exec_4tuple = command_exec_server._services(
        {"layer_stack_root": "/tmp/a"},
    )
    assert via_command_exec_4tuple[0] is backend_a.layer_stack
    assert via_command_exec_4tuple[1] is backend_a.occ_client
    assert via_command_exec_4tuple[2] is backend_a.gitignore
