"""DockerProviderAdapter unit tests — Docker SDK fully mocked.

Verifies the adapter translates Protocol calls into the expected docker-py
SDK shape without requiring a running docker daemon.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from sandbox._shared.models import RawExecResult
from sandbox.provider.docker.adapter import DockerProviderAdapter


def _fake_container(
    *,
    id_: str = "c-1",
    name: str = "/sweevo-test",
    image: str = "sweevo:latest",
    labels: dict[str, str] | None = None,
    state_status: str = "running",
    working_dir: str = "/repo",
) -> MagicMock:
    container = MagicMock()
    container.id = id_
    container.name = name
    container.status = state_status
    container.attrs = {
        "Id": id_,
        "Name": name,
        "Config": {
            "Image": image,
            "Labels": labels or {},
            "WorkingDir": working_dir,
            "Env": [],
            "Cmd": ["sleep", "infinity"],
        },
        "State": {"Status": state_status},
    }
    return container


@pytest.fixture
def fake_client() -> MagicMock:
    client = MagicMock()
    client.containers.create.return_value = _fake_container()
    client.containers.get.return_value = _fake_container()
    client.containers.list.return_value = [_fake_container(), _fake_container(id_="c-2", name="/x")]
    client.images.list.return_value = []
    client.info.return_value = {
        "ServerVersion": "24.0.7",
        "ContainersRunning": 1,
        "KernelVersion": "6.5.0",
        "OperatingSystem": "Linux",
    }
    return client


@pytest.fixture
def adapter(fake_client: MagicMock) -> DockerProviderAdapter:
    adapter = DockerProviderAdapter()
    adapter._client = fake_client
    return adapter


def test_get_health_translates_info(adapter: DockerProviderAdapter, fake_client: MagicMock) -> None:
    health = adapter.get_health()
    fake_client.info.assert_called_once()
    assert health["provider"] == "docker"
    assert health["healthy"] is True
    assert health["server_version"] == "24.0.7"


def test_get_health_returns_unhealthy_on_error(adapter: DockerProviderAdapter, fake_client: MagicMock) -> None:
    fake_client.info.side_effect = RuntimeError("daemon down")
    health = adapter.get_health()
    assert health["healthy"] is False
    assert "daemon down" in health["error"]


def test_create_calls_containers_create_with_default_caps(
    adapter: DockerProviderAdapter, fake_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EOS_DOCKER_PRIVILEGED", raising=False)
    monkeypatch.delenv("EOS_DOCKER_NO_PRIVILEGE", raising=False)

    result = adapter.create(name="sb1", image="sweevo:abc", labels={"project_dir": "/repo"})

    fake_client.containers.create.assert_called_once()
    kwargs = fake_client.containers.create.call_args.kwargs
    assert kwargs["image"] == "sweevo:abc"
    assert kwargs["name"] == "sb1"
    assert kwargs["command"] == ["sleep", "infinity"]
    assert kwargs["detach"] is True
    assert kwargs["cap_add"] == ["SYS_ADMIN"]
    assert "seccomp=unconfined" in kwargs["security_opt"]
    assert "apparmor=unconfined" in kwargs["security_opt"]
    assert kwargs["labels"]["managed_by"] == "eos"
    assert kwargs["labels"]["project_dir"] == "/repo"
    # container.start() called
    fake_client.containers.create.return_value.start.assert_called_once()
    assert result["name"] == "sweevo-test"


def test_create_privileged_escape_hatch(
    adapter: DockerProviderAdapter, fake_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EOS_DOCKER_PRIVILEGED", "1")

    adapter.create(name="sb1", image="x:y")

    kwargs = fake_client.containers.create.call_args.kwargs
    assert kwargs["privileged"] is True
    assert "cap_add" not in kwargs


def test_create_no_privilege_escape_hatch(
    adapter: DockerProviderAdapter, fake_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EOS_DOCKER_PRIVILEGED", raising=False)
    monkeypatch.setenv("EOS_DOCKER_NO_PRIVILEGE", "1")

    adapter.create(name="sb1", image="x:y")

    kwargs = fake_client.containers.create.call_args.kwargs
    assert "privileged" not in kwargs
    assert "cap_add" not in kwargs


def test_create_requires_image_or_snapshot(adapter: DockerProviderAdapter) -> None:
    with pytest.raises(ValueError, match="image"):
        adapter.create(name="sb1")


def test_start_stop_delete(adapter: DockerProviderAdapter, fake_client: MagicMock) -> None:
    container = fake_client.containers.get.return_value
    adapter.start("c-1")
    container.start.assert_called()
    adapter.stop("c-1")
    container.stop.assert_called()
    adapter.delete("c-1")
    container.remove.assert_called_with(force=True)


def test_list_filters_by_managed_by_label(
    adapter: DockerProviderAdapter, fake_client: MagicMock
) -> None:
    out = adapter.list()
    fake_client.containers.list.assert_called_once()
    kwargs = fake_client.containers.list.call_args.kwargs
    assert kwargs["filters"] == {"label": "managed_by=eos"}
    assert len(out) == 2


def test_get_signed_preview_url_shape(adapter: DockerProviderAdapter) -> None:
    result = adapter.get_signed_preview_url("any", 8080)
    assert result == {"url": None, "reason": "docker provider has no signed preview URL"}


def test_get_build_logs_url_returns_none(adapter: DockerProviderAdapter) -> None:
    assert adapter.get_build_logs_url("any") is None


def test_exec_returns_raw_exec_result(adapter: DockerProviderAdapter, fake_client: MagicMock) -> None:
    container = fake_client.containers.get.return_value
    container.exec_run.return_value = (0, (b"hello\n", b""))

    result = asyncio.run(adapter.exec("c-1", "echo hello"))

    assert isinstance(result, RawExecResult)
    assert result.exit_code == 0
    assert result.success is True
    assert result.stdout == "hello\n"
    container.exec_run.assert_called_once()
    cmd = container.exec_run.call_args.kwargs["cmd"]
    assert cmd[:2] == ["/bin/bash", "-lc"]
    assert "echo hello" in cmd[2]


def test_exec_with_cwd_wraps_command(adapter: DockerProviderAdapter, fake_client: MagicMock) -> None:
    container = fake_client.containers.get.return_value
    container.exec_run.return_value = (0, (b"", b""))

    asyncio.run(adapter.exec("c-1", "ls", cwd="/repo"))

    cmd = container.exec_run.call_args.kwargs["cmd"]
    assert cmd[2].startswith("cd /repo && ")
    assert "(ls)" in cmd[2]


def test_exec_nonzero_exit_propagates(adapter: DockerProviderAdapter, fake_client: MagicMock) -> None:
    container = fake_client.containers.get.return_value
    container.exec_run.return_value = (2, (b"", b"boom\n"))

    result = asyncio.run(adapter.exec("c-1", "false"))

    assert result.exit_code == 2
    assert result.success is False
    assert result.stderr == "boom\n"


def test_context_preparer_returns_preparer_instance(adapter: DockerProviderAdapter) -> None:
    preparer = adapter.context_preparer("c-1")
    from sandbox.provider.docker.exec_context import DockerContextPreparer

    assert isinstance(preparer, DockerContextPreparer)
    assert preparer.sandbox_id == "c-1"
