"""Unit tests for the eager in-sandbox runtime bootstrap hook.

Covers the helpers in :mod:`sandbox.control.ops.setup`:

* :func:`bootstrap_in_sandbox_runtime` no-ops when the flag is off,
  sandbox id is missing, or workspace is empty; uploads when the flag is set.
* :func:`maybe_run_eager_runtime_bootstrap` skips/invokes based on flag +
  workspace_root presence.
* :func:`maybe_start_eager_runtime_bundle_upload` /
  :func:`finish_eager_runtime_bundle_upload` background overlap and
  swallowing semantics.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_SANDBOX_RUNTIME_BOOTSTRAP", "1")


@pytest.fixture
def flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EOS_SANDBOX_RUNTIME_BOOTSTRAP", raising=False)


# ---------------------------------------------------------------------------
# bootstrap_in_sandbox_runtime
# ---------------------------------------------------------------------------


def test_bootstrap_helper_noop_when_flag_off(flag_off: None) -> None:
    from sandbox.control.ops.setup import bootstrap_in_sandbox_runtime

    asyncio.run(
        bootstrap_in_sandbox_runtime(
            sandbox_id="sb-1",
            workspace_root="/ws",
        )
    )  # No exception, upload helper is gated by the flag.


def test_bootstrap_helper_uploads_by_sandbox_id(flag_on: None) -> None:
    from sandbox.control.ops.setup import bootstrap_in_sandbox_runtime

    calls: list[str] = []

    async def fake_upload(sandbox_id: str) -> str:
        calls.append(sandbox_id)
        return "deadbeef"

    with patch("sandbox.control.daemon.bundle.ensure_runtime_uploaded", new=fake_upload):
        asyncio.run(
            bootstrap_in_sandbox_runtime(
                sandbox_id="sb-1",
                workspace_root="/ws",
            )
        )

    assert calls == ["sb-1"]


def test_bootstrap_helper_noop_when_workspace_empty(flag_on: None) -> None:
    from sandbox.control.ops.setup import bootstrap_in_sandbox_runtime

    asyncio.run(
        bootstrap_in_sandbox_runtime(
            sandbox_id="sb-1",
            workspace_root="",
        )
    )


def test_bootstrap_helper_raises_on_runtime_upload_failure(flag_on: None) -> None:
    from sandbox.control.ops.setup import bootstrap_in_sandbox_runtime

    async def fail_upload(*_: Any, **__: Any) -> str:
        raise RuntimeError("runtime unavailable")

    with patch("sandbox.control.daemon.bundle.ensure_runtime_uploaded", new=fail_upload), pytest.raises(
        RuntimeError, match="runtime unavailable"
    ):
        asyncio.run(
            bootstrap_in_sandbox_runtime(
                sandbox_id="sb-1",
                workspace_root="/ws",
            )
        )


# ---------------------------------------------------------------------------
# maybe_run_eager_runtime_bootstrap (sync entry point, now in control.ops.setup)
# ---------------------------------------------------------------------------


def test_maybe_bootstrap_skips_when_flag_off(flag_off: None) -> None:
    from sandbox.control.ops.setup import maybe_run_eager_runtime_bootstrap

    sentinel_called = {"called": False}

    async def boom(*_: Any, **__: Any) -> None:
        sentinel_called["called"] = True

    with patch(
        "sandbox.control.ops.setup.bootstrap_in_sandbox_runtime",
        new=boom,
    ):
        maybe_run_eager_runtime_bootstrap("sb-1", "/ws")
    assert sentinel_called["called"] is False


def test_maybe_bootstrap_skips_when_workspace_unresolvable(
    flag_on: None,
) -> None:
    from sandbox.control.ops.setup import maybe_run_eager_runtime_bootstrap

    sentinel_called = {"called": False}

    async def boom(*_: Any, **__: Any) -> None:
        sentinel_called["called"] = True

    with patch(
        "sandbox.control.ops.setup.bootstrap_in_sandbox_runtime",
        new=boom,
    ):
        maybe_run_eager_runtime_bootstrap("sb-1", None)
    assert sentinel_called["called"] is False


def test_maybe_bootstrap_invokes_helper_when_flag_on(
    flag_on: None,
) -> None:
    from sandbox.control.ops.setup import maybe_run_eager_runtime_bootstrap

    calls: list[dict[str, Any]] = []

    async def fake_helper(sandbox_id: str, workspace_root: str) -> None:
        calls.append(
            {
                "sandbox_id": sandbox_id,
                "workspace_root": workspace_root,
            }
        )

    with patch(
        "sandbox.control.ops.setup.bootstrap_in_sandbox_runtime",
        new=fake_helper,
    ):
        maybe_run_eager_runtime_bootstrap("sb-1", "/ws")

    assert len(calls) == 1
    assert calls[0]["sandbox_id"] == "sb-1"
    assert calls[0]["workspace_root"] == "/ws"


def test_maybe_bootstrap_propagates_runtime_upload_error(flag_on: None) -> None:
    from sandbox.control.ops.setup import maybe_run_eager_runtime_bootstrap

    async def fake_helper(*_: Any, **__: Any) -> None:
        raise RuntimeError("runtime crashed")

    with patch(
        "sandbox.control.ops.setup.bootstrap_in_sandbox_runtime",
        new=fake_helper,
    ), pytest.raises(RuntimeError, match="runtime crashed"):
        maybe_run_eager_runtime_bootstrap("sb-1", "/ws")


# ---------------------------------------------------------------------------
# bootstrap_upload_runtime_bundle (background-upload phase)
# ---------------------------------------------------------------------------


def test_upload_helper_noop_when_flag_off(flag_off: None) -> None:
    from sandbox.control.ops.setup import bootstrap_upload_runtime_bundle

    asyncio.run(
        bootstrap_upload_runtime_bundle(
            sandbox_id="sb-1",
            workspace_root="/ws",
        )
    )


def test_upload_helper_noop_on_missing_inputs(flag_on: None) -> None:
    from sandbox.control.ops.setup import bootstrap_upload_runtime_bundle

    for missing in ({"sandbox_id": ""}, {"workspace_root": ""}):
        kwargs: dict[str, Any] = {
            "sandbox_id": "sb-1",
            "workspace_root": "/ws",
        }
        kwargs.update(missing)
        asyncio.run(bootstrap_upload_runtime_bundle(**kwargs))


def test_upload_helper_uploads_without_running_lifecycle_bootstrap(
    flag_on: None,
) -> None:
    """Background upload runs ensure_runtime_uploaded directly."""
    from sandbox.control.ops.setup import bootstrap_upload_runtime_bundle

    upload_calls: list[str] = []

    async def fake_upload(sandbox_id: str) -> str:
        upload_calls.append(sandbox_id)
        return "deadbeef"

    with patch(
        "sandbox.control.daemon.bundle.ensure_runtime_uploaded",
        new=fake_upload,
    ):
        asyncio.run(
            bootstrap_upload_runtime_bundle(
                sandbox_id="sb-1",
                workspace_root="/ws",
            )
        )

    assert upload_calls == ["sb-1"]


# ---------------------------------------------------------------------------
# maybe_start_eager_runtime_bundle_upload / finish_eager_runtime_bundle_upload
# ---------------------------------------------------------------------------


def test_start_upload_returns_none_when_flag_off(flag_off: None) -> None:
    from sandbox.control.ops.setup import maybe_start_eager_runtime_bundle_upload

    assert maybe_start_eager_runtime_bundle_upload("sb-1", "/ws") is None


def test_start_upload_returns_none_when_workspace_missing(flag_on: None) -> None:
    from sandbox.control.ops.setup import maybe_start_eager_runtime_bundle_upload

    assert maybe_start_eager_runtime_bundle_upload("sb-1", None) is None


def test_start_upload_submits_future_and_invokes_helper(flag_on: None) -> None:
    """Future resolves successfully when the background upload completes."""
    import threading

    from sandbox.control.ops.setup import (
        finish_eager_runtime_bundle_upload,
        maybe_start_eager_runtime_bundle_upload,
    )

    helper_done = threading.Event()
    helper_args: dict[str, Any] = {}

    async def fake_helper(sandbox_id: str, workspace_root: str) -> None:
        helper_args.update(
            sandbox_id=sandbox_id,
            workspace_root=workspace_root,
        )
        helper_done.set()

    with patch(
        "sandbox.control.ops.setup.bootstrap_upload_runtime_bundle",
        new=fake_helper,
    ):
        future = maybe_start_eager_runtime_bundle_upload("sb-1", "/ws")
        assert future is not None
        # Caller drains the future; success path must not raise.
        finish_eager_runtime_bundle_upload(future, "sb-1")

    assert helper_done.is_set()
    assert helper_args == {
        "sandbox_id": "sb-1",
        "workspace_root": "/ws",
    }


def test_finish_upload_swallows_helper_failure(flag_on: None) -> None:
    """Background failure must not propagate — sequential bootstrap retries."""
    from sandbox.control.ops.setup import (
        finish_eager_runtime_bundle_upload,
        maybe_start_eager_runtime_bundle_upload,
    )

    async def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("upload exploded")

    with patch(
        "sandbox.control.ops.setup.bootstrap_upload_runtime_bundle",
        new=boom,
    ):
        future = maybe_start_eager_runtime_bundle_upload("sb-1", "/ws")
        assert future is not None
        finish_eager_runtime_bundle_upload(future, "sb-1")  # MUST NOT raise


def test_finish_upload_noop_when_future_none() -> None:
    from sandbox.control.ops.setup import finish_eager_runtime_bundle_upload

    finish_eager_runtime_bundle_upload(None, "sb-1")  # MUST NOT raise
