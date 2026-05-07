"""Phase 05 — write/edit/read dispatch + classifier predicate tests.

Covers the §6 classifier-predicate bullets, the single-path contract,
the OP_TABLE wiring, and the shared-LeaseRegistry assertion.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from sandbox.layer_stack.workspace_base import build_workspace_base
from sandbox.runtime import (
    api_handlers,
    server,
)
from sandbox.runtime.handlers import (
    edit_handler,
    read_handler,
    shell_handler,
    write_handler,
)
from sandbox.runtime.handlers._common import (
    ClassifiedPath,
    _services,
    _services_cache_clear,
    classify_path,
)
from sandbox.runtime.layer_stack_server import get_layer_stack_manager


# ---------------------------------------------------------------------------
# Classifier predicate
# ---------------------------------------------------------------------------


def test_classify_workspace_relative_path_in_workspace(tmp_path: Path) -> None:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    result = classify_path("foo", workspace.as_posix())
    assert result.classification == "in_workspace"
    assert Path(result.abs_path).resolve() == (workspace / "foo").resolve()
    assert result.layer_path == "foo"


def test_classify_absolute_workspace_path_in_workspace(tmp_path: Path) -> None:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    result = classify_path((workspace / "foo").as_posix(), workspace.as_posix())
    assert result.classification == "in_workspace"
    assert result.layer_path == "foo"


def test_classify_relative_and_absolute_resolve_to_same_layer_path(
    tmp_path: Path,
) -> None:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    relative = classify_path("nested/a.py", workspace.as_posix())
    absolute = classify_path(
        (workspace / "nested" / "a.py").as_posix(),
        workspace.as_posix(),
    )
    assert relative.layer_path == absolute.layer_path == "nested/a.py"


def test_classify_symlink_to_outside_workspace_classifies_out(
    tmp_path: Path,
) -> None:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    target = (tmp_path / "outside").resolve()
    target.mkdir()
    (target / "foo").write_text("x")
    (workspace / "link").symlink_to(target / "foo")
    result = classify_path((workspace / "link").as_posix(), workspace.as_posix())
    assert result.classification == "out_of_workspace"
    assert Path(result.abs_path).resolve() == (target / "foo").resolve()


def test_classify_symlink_inside_workspace_classifies_in(
    tmp_path: Path,
) -> None:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    inner = workspace / "inner"
    inner.mkdir()
    (inner / "foo").write_text("x")
    (workspace / "link").symlink_to(inner / "foo")
    result = classify_path((workspace / "link").as_posix(), workspace.as_posix())
    assert result.classification == "in_workspace"
    assert result.layer_path == "inner/foo"


def test_classify_dotdot_escape_is_hard_error(tmp_path: Path) -> None:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    with pytest.raises(ValueError, match="escapes workspace"):
        classify_path((workspace / ".." / "etc" / "passwd").as_posix(), workspace.as_posix())
    with pytest.raises(ValueError, match="escapes workspace"):
        classify_path("../etc/passwd", workspace.as_posix())


def test_classify_outside_absolute_path_classifies_out_of_workspace(
    tmp_path: Path,
) -> None:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    result = classify_path("/tmp/foo", workspace.as_posix())
    assert result.classification == "out_of_workspace"
    assert isinstance(result, ClassifiedPath)


# ---------------------------------------------------------------------------
# OP_TABLE wiring (write/edit/read dispatch from runtime.handlers)
# ---------------------------------------------------------------------------


def test_op_table_dispatches_write_edit_read_to_write_edit_handlers() -> None:
    server._load_peer_bootstraps()
    assert server.OP_TABLE["api.write_file"] is write_handler.write_file
    assert server.OP_TABLE["api.edit_file"] is edit_handler.edit_file
    assert server.OP_TABLE["api.read_file"] is read_handler.read_file
    assert server.OP_TABLE["api.shell"] is shell_handler.shell
    assert server.OP_TABLE["api.layer_metrics"] is api_handlers.layer_metrics


def test_api_handlers_no_longer_exposes_write_edit_read() -> None:
    """api_handlers shrank to layer_metrics + service-cache helpers."""
    assert not hasattr(api_handlers, "write_file")
    assert not hasattr(api_handlers, "edit_file")
    assert not hasattr(api_handlers, "read_file")
    # Bucket commit-gate primitives are gone.
    assert not hasattr(api_handlers, "_process_commit_gate")
    assert not hasattr(api_handlers, "_commit_lock")
    assert not hasattr(api_handlers, "_PROCESS_COMMIT_BUCKETS")
    assert not hasattr(api_handlers, "_PROCESS_COMMIT_LOCK_BUCKETS")


# ---------------------------------------------------------------------------
# Single-path contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_rejects_list_path_argument(tmp_path: Path) -> None:
    _services_cache_clear()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    with pytest.raises(ValueError, match="single-path contract"):
        await write_handler.write_file(
            {
                "layer_stack_root": stack.as_posix(),
                "path": ["a", "b"],
                "content": "x",
            }
        )


@pytest.mark.asyncio
async def test_edit_file_rejects_list_path_argument(tmp_path: Path) -> None:
    _services_cache_clear()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    with pytest.raises(ValueError, match="single-path contract"):
        await edit_handler.edit_file(
            {
                "layer_stack_root": stack.as_posix(),
                "path": ["a", "b"],
                "edits": [{"old_text": "x", "new_text": "y"}],
            }
        )


# ---------------------------------------------------------------------------
# Shared LeaseRegistry across shell + write/edit/read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_edit_read_share_lease_registry_with_shell(
    tmp_path: Path,
) -> None:
    """All four flows acquire leases from the SAME registry instance — layer-stack
    GC sees a unified pin set."""
    from sandbox.runtime import command_exec_server

    _services_cache_clear()
    command_exec_server._services_cache_clear()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("base\n", encoding="utf-8")
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)

    write_services = _services(stack.as_posix())
    manager_via_singleton = get_layer_stack_manager(stack.as_posix())

    # The write/edit/read services point at the same LayerStackManager singleton
    # as the shell path; the LeaseRegistry is internal to that manager, so all
    # four flows pin layers through one registry.
    assert write_services.manager is manager_via_singleton
    assert write_services.layer_stack.manager is manager_via_singleton

    # Active counts reflect a single registry: a fresh acquire bumps the count
    # observed by the OTHER consumer.
    starting_active = manager_via_singleton.active_lease_count()
    lease = manager_via_singleton.acquire_snapshot_lease("test")
    try:
        assert manager_via_singleton.active_lease_count() == starting_active + 1
        # Same view across the in-process service cache.
        assert (
            write_services.manager.active_lease_count()
            == manager_via_singleton.active_lease_count()
        )
    finally:
        manager_via_singleton.release_lease(lease.lease_id)


@pytest.mark.asyncio
async def test_in_workspace_write_pins_lease_then_releases(tmp_path: Path) -> None:
    """An in-workspace write_file holds a lease covering prepare → publish."""
    _services_cache_clear()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "seed.txt").write_text("seed\n", encoding="utf-8")
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    manager = get_layer_stack_manager(stack.as_posix())
    starting_count = manager.active_lease_count()

    result = await write_handler.write_file(
        {
            "layer_stack_root": stack.as_posix(),
            "path": "new.txt",
            "content": "fresh\n",
            "actor_id": f"agent-{uuid4().hex[:6]}",
        }
    )

    assert result["success"] is True
    # Lease is released after publish — in-flight count returns to baseline.
    assert manager.active_lease_count() == starting_count


# ---------------------------------------------------------------------------
# Sanity: real read_file in-workspace returns layer-stack bytes (not real FS)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_in_workspace_returns_layer_stack_bytes(
    tmp_path: Path,
) -> None:
    _services_cache_clear()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("base\n", encoding="utf-8")
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    # Mutate the real workspace file AFTER base build — read_file must NOT see this
    (workspace / "a.txt").write_text("mutated\n", encoding="utf-8")

    result = await read_handler.read_file(
        {
            "layer_stack_root": stack.as_posix(),
            "path": "a.txt",
        }
    )

    assert result["success"] is True
    assert result["exists"] is True
    assert result["content"] == "base\n"


def test_classifier_resolves_workspace_real_path_when_input_uses_literal(
    tmp_path: Path,
) -> None:
    """When workspace_root is a symlink, literal-prefixed input still classifies in."""
    real_workspace = (tmp_path / "real-ws").resolve()
    real_workspace.mkdir()
    link_workspace = tmp_path / "ws"
    os.symlink(real_workspace, link_workspace)
    # Pass the LITERAL (symlink) workspace path; input uses literal-prefixed form.
    result = classify_path(f"{link_workspace}/foo", str(link_workspace))
    assert result.classification == "in_workspace"
    assert result.layer_path == "foo"
