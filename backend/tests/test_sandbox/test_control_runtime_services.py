"""Tests for process-local sandbox runtime service bindings."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.api import ReadFileRequest, ShellRequest, WriteFileRequest
from sandbox.api.tool.read import read_file
from sandbox.api.tool.shell import shell
from sandbox.api.tool.write import write_file
from sandbox.control.ops.runtime_services import create_runtime_services
from sandbox.occ.client import OCCClientError, get_occ_service
from sandbox.overlay.client import OverlayClientError, get_overlay_client
from sandbox.providers.registry import has_registered_adapter


async def test_runtime_services_bind_public_api_verbs(tmp_path: Path) -> None:
    services = create_runtime_services(
        sandbox_id="sb-runtime-services",
        storage_root=tmp_path / "layer-stack",
    )
    try:
        actor = services.actor("unit")
        write = await write_file(
            services.sandbox_id,
            WriteFileRequest(
                path="src/a.txt",
                content="a\n",
                actor=actor,
            ),
        )
        read = await read_file(
            services.sandbox_id,
            ReadFileRequest(path="src/a.txt", actor=actor),
        )
        command = await shell(
            services.sandbox_id,
            ShellRequest(
                command="mkdir -p src; printf 'b\\n' > src/b.txt; cat src/b.txt",
                actor=actor,
                timeout=10,
            ),
        )

        assert write.success is True
        assert read.success is True
        assert read.content == "a\n"
        assert command.success is True
        assert command.stdout == "b\n"
        assert command.changed_paths == ("src/b.txt",)
        assert services.manager.read_text("src/b.txt") == ("b\n", True)
    finally:
        services.dispose()


def test_runtime_services_dispose_removes_bindings(tmp_path: Path) -> None:
    sandbox_id = "sb-runtime-services-dispose"
    services = create_runtime_services(
        sandbox_id=sandbox_id,
        storage_root=tmp_path / "layer-stack",
    )

    assert has_registered_adapter(sandbox_id) is True
    assert get_occ_service(sandbox_id) is not None
    assert get_overlay_client(sandbox_id) is not None

    services.dispose()

    assert has_registered_adapter(sandbox_id) is False
    with pytest.raises(OCCClientError):
        get_occ_service(sandbox_id)
    with pytest.raises(OverlayClientError):
        get_overlay_client(sandbox_id)
