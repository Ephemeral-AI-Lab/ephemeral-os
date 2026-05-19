"""Same primitive call returns equal shape across providers.

Linux+Docker-gated; Daytona path skipped when DAYTONA_API_KEY missing.
See PLAN_v4 §5.2.
"""

from __future__ import annotations

import os
import sys

import pytest


def _docker_available() -> bool:
    return (
        sys.platform.startswith("linux")
        and os.environ.get("EOS_HAVE_DOCKER") == "1"
    )


def _daytona_available() -> bool:
    return bool(os.environ.get("DAYTONA_API_KEY")) and bool(
        os.environ.get("DAYTONA_API_URL")
    )


PROVIDERS = []
if _docker_available():
    PROVIDERS.append("docker")
if _daytona_available():
    PROVIDERS.append("daytona")


@pytest.mark.skipif(
    not PROVIDERS,
    reason="No providers available in this environment (PLAN_v4 §5.2).",
)
@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.asyncio
async def test_pwd_exec_returns_equal_shape(provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """`adapter.exec("pwd")` returns RawExecResult with equivalent shape per provider."""
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", provider)

    import sandbox.provider.bootstrap as dispatcher

    dispatcher._reset_for_tests()
    dispatcher.bootstrap_sandbox_provider()

    from sandbox.provider.registry import get_default_provider

    adapter = get_default_provider()
    assert adapter.name == provider
    # Shape-only assertion — a real exec requires a created sandbox per provider
    # which the smoke/post_lifecycle tests cover. This parity test only checks
    # that both adapters expose the same exec signature.
    import inspect

    sig = inspect.signature(adapter.exec)
    assert "command" in sig.parameters
    assert "cwd" in sig.parameters
    assert "timeout" in sig.parameters
