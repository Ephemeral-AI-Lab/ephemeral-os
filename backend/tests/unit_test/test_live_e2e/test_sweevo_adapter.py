from __future__ import annotations

from types import SimpleNamespace

from live_e2e import sweevo_adapter


def test_sweevo_auto_reuse_is_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("EOS_SWEEVO_REUSE_SANDBOX", raising=False)
    monkeypatch.delenv("EOS_SWEEVO_FORCE_FRESH_SANDBOX", raising=False)

    assert sweevo_adapter._reuse_existing_auto_enabled() is False

    monkeypatch.setenv("EOS_SWEEVO_REUSE_SANDBOX", "1")
    assert sweevo_adapter._reuse_existing_auto_enabled() is True


def test_sweevo_force_fresh_overrides_reuse(monkeypatch) -> None:
    monkeypatch.setenv("EOS_SWEEVO_REUSE_SANDBOX", "1")
    monkeypatch.setenv("EOS_SWEEVO_FORCE_FRESH_SANDBOX", "1")

    assert sweevo_adapter._reuse_existing_auto_enabled() is False


def test_workspace_used_sandboxes_are_session_local() -> None:
    first_session = SimpleNamespace()
    second_session = SimpleNamespace()

    first_seen = sweevo_adapter._session_workspace_used_sandboxes(first_session)
    first_seen.add("sandbox-a")

    assert sweevo_adapter._session_workspace_used_sandboxes(first_session) == {
        "sandbox-a"
    }
    assert sweevo_adapter._session_workspace_used_sandboxes(second_session) == set()
