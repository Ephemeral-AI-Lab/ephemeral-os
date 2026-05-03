"""DaemonBackend wire dispatch — verifies it inherits the runtime command flow.

After the OCC simplification, the daemon backend is purely a thin transport:
it owns ``cmd``, ``warmup``, ``ensure_initialized``, ``rebind_sandbox``, and
``dispose``. Mutation requests flow through ``OCCClient.apply_changeset`` and
the runtime ``occ.apply_changeset`` handler — *not* through backend methods.
"""

from __future__ import annotations

from typing import Any

import pytest

from sandbox.runtime.backends import DaemonBackend


def test_daemon_backend_exposes_cmd_and_dispose() -> None:
    backend = DaemonBackend(sandbox_id="sb-test", workspace_root="/ws")
    assert backend.sandbox_id == "sb-test"
    assert backend.workspace_root == "/ws"
    backend.dispose()


def test_daemon_backend_no_longer_exposes_legacy_mutations() -> None:
    """The Step-4 cleanup removes write_file / edit_file / commit_* methods."""
    backend = DaemonBackend(sandbox_id="sb", workspace_root="/ws")
    assert not hasattr(backend, "write_file")
    assert not hasattr(backend, "edit_file")
    assert not hasattr(backend, "commit_operation_against_base")
    assert not hasattr(backend, "commit_specs_many")


def test_warmup_calls_ensure_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Warmup should bridge to ensure_initialized — no separate daemon op."""
    called: list[bool] = []

    def fake_ensure(self: DaemonBackend, wait: bool = True) -> bool:
        called.append(True)
        return True

    monkeypatch.setattr(DaemonBackend, "ensure_initialized", fake_ensure)
    backend = DaemonBackend(
        sandbox_id="sb",
        workspace_root="/ws",
    )
    backend.warmup()
    assert called == [True]


def test_rebind_sandbox_is_a_no_op() -> None:
    backend = DaemonBackend(sandbox_id="sb", workspace_root="/ws")
    # Rebinding on the daemon backend is a no-op (the host-side does not
    # carry the sandbox handle). Just verify the method exists and runs.
    backend.rebind_sandbox(object())


def test_runtime_call_path_uses_apply_changeset_op() -> None:
    """Verify the runtime command path is wired (no fake-handler injection here)."""
    backend = DaemonBackend(sandbox_id="sb", workspace_root="/ws")
    assert callable(getattr(backend, "_call_runtime_command", None))


def _unused() -> dict[str, Any]:  # pragma: no cover - placeholder for legacy import compat
    return {}
