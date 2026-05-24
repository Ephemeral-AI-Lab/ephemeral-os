"""Phase 2.6 C4 wire-protocol round-trip test.

After C4 collapsed ``isolated_workspace/handlers.py`` into 5 inline
``_iws_*`` functions on the dispatcher module, the dispatcher's OP_TABLE
is the only public surface for the ``api.isolated_workspace.*`` ops.
This test pins the five envelope shapes (success + failure) so a future
refactor that mistakenly changes the response keys will surface as a
red regression rather than a silent wire break.

Routing-only test: we do NOT bootstrap a real pipeline. The pipeline
singleton is left unset so each handler exercises its
``feature_disabled`` / ``invalid_argument`` failure path — that's the
cheap, deterministic round-trip the test gate needs.
"""

from __future__ import annotations

import asyncio
from typing import Any


def _drop_active_iws_pipeline() -> None:
    from sandbox.isolated_workspace.helper import manager as iws_manager

    iws_manager.set_pipeline(None)


def _op(op_name: str) -> Any:
    from sandbox.daemon.rpc.dispatcher import OP_TABLE

    handler = OP_TABLE.get(op_name)
    assert handler is not None, f"missing OP_TABLE entry for {op_name}"
    return handler


def test_iws_rpc_envelopes_pinned() -> None:
    _drop_active_iws_pipeline()

    expected_envelopes = {
        "api.isolated_workspace.enter": (
            {"layer_stack_root": "", "agent_id": ""},
            {"success", "error"},
        ),
        "api.isolated_workspace.exit": (
            {"agent_id": ""},
            {"success", "error"},
        ),
        "api.isolated_workspace.status": (
            {"agent_id": ""},
            {"success", "error"},
        ),
        "api.isolated_workspace.list_open": (
            {},
            {"success", "open_agent_ids"},
        ),
        "api.isolated_workspace.test_reset": (
            {},
            {"success", "error"},
        ),
    }

    for op_name, (args, expected_top_keys) in expected_envelopes.items():
        handler = _op(op_name)
        response = asyncio.run(handler(args))
        assert isinstance(response, dict), f"{op_name} must return dict"
        assert "success" in response, f"{op_name} missing success key"
        top_keys = set(response.keys())
        assert expected_top_keys.issubset(top_keys), (
            f"{op_name} envelope shape regressed: "
            f"missing={expected_top_keys - top_keys}, got={sorted(top_keys)}"
        )
        if not response["success"]:
            error = response.get("error")
            assert isinstance(error, dict), f"{op_name} error must be dict"
            for required in ("kind", "message", "details"):
                assert required in error, (
                    f"{op_name} error envelope missing {required!r}; "
                    f"got keys={sorted(error.keys())}"
                )


def test_iws_op_table_keys_are_exhaustive() -> None:
    from sandbox.daemon.rpc.dispatcher import OP_TABLE

    iws_ops = {op for op in OP_TABLE if op.startswith("api.isolated_workspace.")}
    assert iws_ops == {
        "api.isolated_workspace.enter",
        "api.isolated_workspace.exit",
        "api.isolated_workspace.status",
        "api.isolated_workspace.list_open",
        "api.isolated_workspace.test_reset",
    }, f"unexpected iws op set: {sorted(iws_ops)}"


def test_status_returns_open_false_when_pipeline_present_but_no_handle(
    monkeypatch,
) -> None:
    """Routing reaches ``manager.get_handle`` and returns the open=False shape.

    Pins the success-path envelope for status; the other handlers'
    success-path shapes are exercised by the live e2e tier.
    """
    from sandbox.daemon.rpc import dispatcher
    from sandbox.isolated_workspace.helper import manager as iws_manager

    class _StubManager:
        def get_handle(self, _agent_id: str):
            return None

    monkeypatch.setattr(iws_manager, "require_pipeline", lambda: _StubManager())

    response = asyncio.run(
        dispatcher.OP_TABLE["api.isolated_workspace.status"](
            {"agent_id": "agent-x"}
        )
    )
    assert response == {"success": True, "open": False}
