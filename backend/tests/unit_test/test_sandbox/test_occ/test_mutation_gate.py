"""Phase 05 — OCC mutation gate structural surface + retry-bound tests.

These tests assert the §6 structural invariants:

* occ-server's externally-reachable wire methods are exactly
  ``{apply_changeset, start, stop, health}``.
* No ``api.*`` / ``write_*`` / ``edit_*`` / ``read_*`` symbols appear on
  occ-server.
* In-workspace classifier predicate lives in command-exec only;
  occ-server source contains no ``workspace_root`` classification call
  sites.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.runtime import occ_handlers, occ_server


# ---------------------------------------------------------------------------
# Structural surface
# ---------------------------------------------------------------------------


def test_occ_op_table_surface_is_exactly_four_methods() -> None:
    """occ-server's wire methods must equal {apply_changeset,start,stop,health}."""
    assert set(occ_handlers.OCC_OP_TABLE) == {
        "apply_changeset",
        "start",
        "stop",
        "health",
    }


def test_occ_op_table_carries_no_api_prefixed_symbols() -> None:
    for op in occ_handlers.OCC_OP_TABLE:
        assert not op.startswith("api.")
        assert not op.startswith("write_")
        assert not op.startswith("edit_")
        assert not op.startswith("read_")


def test_occ_handlers_module_does_not_classify_paths() -> None:
    """occ-server must not own the in-workspace classifier — single source of
    truth lives on command-exec (handlers/_common.py)."""
    occ_handlers_source = Path(occ_handlers.__file__).read_text()
    occ_server_source = Path(occ_server.__file__).read_text()

    # No literal ``workspace_root`` classification call sites in occ-server.
    # (Comments may reference the concept; the assertion is on actual code:
    # there must be no attribute access or comparison against workspace_root.)
    for source in (occ_handlers_source, occ_server_source):
        # Allow workspace_root in docstring/comments only — strip both before checking.
        # Quick approximation: scan for runtime identifiers like
        # ``.workspace_root`` or ``workspace_root =``.
        assert ".workspace_root" not in source
        assert "workspace_root =" not in source
        assert "workspace_root==" not in source


def test_occ_handlers_does_not_register_api_ops_against_runtime_dispatcher() -> None:
    """occ_handlers must never put api.write_*/edit_*/read_* into runtime.OP_TABLE."""
    from sandbox.runtime import server

    server._load_peer_bootstraps()
    # The host-facing api.* ops must dispatch to runtime.handlers.*,
    # never to occ_handlers callables.
    for op in ("api.write_file", "api.edit_file", "api.read_file"):
        handler = server.OP_TABLE[op]
        assert handler.__module__ != occ_handlers.__name__


# ---------------------------------------------------------------------------
# Lifecycle stubs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_health_returns_ok() -> None:
    response = await occ_handlers.health()
    assert response["status"] == "ok"


@pytest.mark.asyncio
async def test_lifecycle_start_stop_round_trip() -> None:
    started = await occ_handlers.start()
    assert started["status"] == "ok" and started["running"] is True
    stopped = await occ_handlers.stop()
    assert stopped["status"] == "ok" and stopped["running"] is False


# ---------------------------------------------------------------------------
# Apply forwarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_changeset_forwards_to_occ_client() -> None:
    """apply_changeset is a thin wrapper that delegates to OCCClient."""

    class _StubClient:
        def __init__(self) -> None:
            self.called_with: dict | None = None

        async def apply_changeset(
            self,
            typed_changes,
            *,
            snapshot=None,
            options=None,
            workspace_ref=None,
        ) -> str:
            self.called_with = {
                "changes": tuple(typed_changes),
                "snapshot": snapshot,
                "options": options,
                "workspace_ref": workspace_ref,
            }
            return "delegated"

    client = _StubClient()
    result = await occ_handlers.apply_changeset(
        client,  # type: ignore[arg-type]
        ["change-A"],
        snapshot="manifest",
        options="opts",
        workspace_ref="ws-ref",
    )
    assert result == "delegated"
    assert client.called_with == {
        "changes": ("change-A",),
        "snapshot": "manifest",
        "options": "opts",
        "workspace_ref": "ws-ref",
    }


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

    def always_cas_mismatch(*args, **kwargs):
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
        lambda layer_stack: ("oracle", layer_stack),
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

    # command_exec_server returns a 4-tuple; the first three fields
    # identity-match the cached OccBackend's fields.
    via_command_exec_4tuple = command_exec_server._services(
        {"layer_stack_root": "/tmp/a"},
    )
    assert via_command_exec_4tuple[0] is backend_a.layer_stack
    assert via_command_exec_4tuple[1] is backend_a.occ_client
    assert via_command_exec_4tuple[2] is backend_a.gitignore
