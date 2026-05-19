"""Both adapters expose every method on ``ProviderAdapter``."""

from __future__ import annotations

from sandbox.provider.daytona.adapter import DaytonaProviderAdapter
from sandbox.provider.docker.adapter import DockerProviderAdapter

_PROTOCOL_METHODS = (
    "get_health",
    "list_snapshots",
    "create",
    "get",
    "list",
    "start",
    "stop",
    "delete",
    "set_labels",
    "get_signed_preview_url",
    "get_build_logs_url",
    "exec",
    "context_preparer",
)


def test_daytona_adapter_implements_protocol() -> None:
    adapter = DaytonaProviderAdapter()
    assert adapter.name == "daytona"
    for method in _PROTOCOL_METHODS:
        assert hasattr(adapter, method), f"DaytonaProviderAdapter missing {method}"
        assert callable(getattr(adapter, method))


def test_docker_adapter_implements_protocol() -> None:
    adapter = DockerProviderAdapter()
    assert adapter.name == "docker"
    for method in _PROTOCOL_METHODS:
        assert hasattr(adapter, method), f"DockerProviderAdapter missing {method}"
        assert callable(getattr(adapter, method))


def test_docker_signed_preview_url_returns_shape() -> None:
    adapter = DockerProviderAdapter()
    result = adapter.get_signed_preview_url("any-id", 8080)
    assert isinstance(result, dict)
    assert result.get("url") is None
    assert isinstance(result.get("reason"), str)
    assert result["reason"]


def test_docker_build_logs_url_returns_none() -> None:
    adapter = DockerProviderAdapter()
    assert adapter.get_build_logs_url("any-id") is None
