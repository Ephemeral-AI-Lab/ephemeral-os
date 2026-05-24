"""Phase 2.6 C3.5a: ``api.release_workspace_snapshot`` is a deprecation alias.

The wire-protocol surface keeps both ``api.release_lease`` (canonical) and
``api.release_workspace_snapshot`` (legacy, scheduled for removal one
release cycle after Phase 2.6 ships — see follow-up §11 item 12). The alias
delegates to the same handler and emits a WARN log so callers can grep CI
output for migration targets.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from unittest.mock import patch

import pytest


def _import_dispatcher_and_handler():
    from sandbox.daemon import handlers as workspace
    from sandbox.daemon.rpc import dispatcher

    return dispatcher, workspace


def test_both_api_names_registered() -> None:
    dispatcher, _ = _import_dispatcher_and_handler()
    assert "api.release_lease" in dispatcher.OP_TABLE
    assert "api.release_workspace_snapshot" in dispatcher.OP_TABLE


def test_alias_handler_is_the_deprecation_shim() -> None:
    dispatcher, workspace = _import_dispatcher_and_handler()
    assert dispatcher.OP_TABLE["api.release_workspace_snapshot"] is (
        workspace.release_workspace_snapshot
    )
    assert dispatcher.OP_TABLE["api.release_lease"] is workspace.release_lease
    assert workspace.release_workspace_snapshot is not workspace.release_lease


def test_alias_delegates_and_logs_deprecation(caplog) -> None:
    _, workspace = _import_dispatcher_and_handler()
    with patch.object(workspace.workspace_server, "release_lease", return_value=True):
        with patch(
            "sandbox.daemon.handlers.require_layer_stack_root",
            return_value="/testbed",
        ), patch(
            "sandbox.daemon.handlers.require_arg", return_value="lease-x",
        ):
            with caplog.at_level(logging.WARNING, logger="sandbox.daemon.handlers"):
                result = asyncio.get_event_loop().run_until_complete(
                    workspace.release_workspace_snapshot({})
                ) if not asyncio.iscoroutinefunction(workspace.release_workspace_snapshot) else asyncio.run(
                    workspace.release_workspace_snapshot({})
                )
    assert result == {"success": True, "released": True}
    assert any(
        "deprecated_alias=api.release_workspace_snapshot" in record.getMessage()
        for record in caplog.records
    ), [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize(
    "alias_name",
    ["release_workspace_snapshot", "release_lease"],
)
def test_handlers_are_coroutines(alias_name: str) -> None:
    _, workspace = _import_dispatcher_and_handler()
    handler = getattr(workspace, alias_name)
    assert inspect.iscoroutinefunction(handler), alias_name
