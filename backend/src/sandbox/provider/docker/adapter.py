"""Docker implementation of :class:`sandbox.provider.protocol.ProviderAdapter`.

All Docker SDK access is lazy via :func:`sandbox.provider.docker.client.get_docker_client`
so this module imports cleanly without the ``docker`` package installed.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Any

from sandbox._shared.models import RawExecResult
from sandbox.provider.docker.client import (
    get_async_docker_client,
    get_docker_client,
    host_config_kwargs,
)

logger = logging.getLogger(__name__)

APP_MANAGED_BY = "eos"
APP_CREATED_VIA = "ephemeral_os"


def _normalize_dict(payload: dict[str, str] | None) -> dict[str, str]:
    if not payload:
        return {}
    return {
        str(k).strip(): str(v).strip()
        for k, v in payload.items()
        if str(k).strip()
    }


def _serialize_container(container: Any) -> dict[str, Any]:
    """Translate ``docker.models.containers.Container`` into our canonical dict."""
    attrs = getattr(container, "attrs", None) or {}
    config = attrs.get("Config") or {}
    state = attrs.get("State") or {}
    labels = config.get("Labels") or {}

    return {
        "id": getattr(container, "id", None) or attrs.get("Id"),
        "name": (getattr(container, "name", None) or attrs.get("Name") or "").lstrip("/"),
        "image": config.get("Image"),
        "snapshot": labels.get("snapshot"),
        "status": state.get("Status") or getattr(container, "status", None),
        "labels": dict(labels),
        "project_dir": labels.get("project_dir") or config.get("WorkingDir"),
    }


def _serialize_image(image: Any) -> dict[str, Any]:
    """Translate ``docker.models.images.Image`` into a Daytona-snapshot-shaped dict."""
    tags = list(getattr(image, "tags", None) or [])
    primary = tags[0] if tags else None
    attrs = getattr(image, "attrs", None) or {}
    return {
        "name": primary,
        "image": primary,
        "id": getattr(image, "id", None) or attrs.get("Id"),
        "tags": tags,
    }


class DockerProviderAdapter:
    """Docker SDK-backed implementation of ``ProviderAdapter``."""

    name = "docker"

    def __init__(self) -> None:
        # Client is constructed lazily so darwin imports don't blow up.
        self._client: Any | None = None

    # -- Client wiring -------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = get_docker_client()
        return self._client

    async def _get_async_client(self) -> Any:
        if self._client is None:
            self._client = await asyncio.to_thread(get_async_docker_client)
        return self._client

    # -- Health / discovery --------------------------------------------------

    def get_health(self) -> dict[str, Any]:
        try:
            info = self._get_client().info()
        except Exception as exc:  # pragma: no cover - depends on local daemon
            return {"provider": "docker", "healthy": False, "error": str(exc)}
        return {
            "provider": "docker",
            "healthy": True,
            "server_version": info.get("ServerVersion"),
            "containers_running": info.get("ContainersRunning"),
            "kernel_version": info.get("KernelVersion"),
            "operating_system": info.get("OperatingSystem"),
        }

    def list_snapshots(self) -> list[dict[str, Any]]:
        try:
            images = self._get_client().images.list()
        except Exception:
            logger.warning("docker.images.list() failed", exc_info=True)
            return []
        return [_serialize_image(img) for img in images]

    # -- Container CRUD ------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        snapshot: str | None = None,
        image: str | None = None,
        language: str = "python",
        env_vars: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        image_ref = (image or snapshot or "").strip()
        if not image_ref:
            raise ValueError("DockerProviderAdapter.create requires `image` or `snapshot`")

        client = self._get_client()

        merged_labels = {
            "managed_by": APP_MANAGED_BY,
            "created_via": APP_CREATED_VIA,
            "language": language,
        }
        if snapshot:
            merged_labels["snapshot"] = snapshot
        merged_labels.update(_normalize_dict(labels))

        host_kwargs = host_config_kwargs()

        container = client.containers.create(
            image=image_ref,
            name=name,
            command=["sleep", "infinity"],
            detach=True,
            tty=False,
            environment=_normalize_dict(env_vars),
            labels=merged_labels,
            **host_kwargs,
        )
        container.start()
        container.reload()
        return _serialize_container(container)

    def get(self, sandbox_id: str) -> dict[str, Any]:
        container = self._get_client().containers.get(sandbox_id)
        container.reload()
        return _serialize_container(container)

    def list(self) -> list[dict[str, Any]]:
        try:
            containers = self._get_client().containers.list(
                all=True, filters={"label": f"managed_by={APP_MANAGED_BY}"}
            )
        except Exception:
            logger.warning("docker.containers.list() failed", exc_info=True)
            return []
        return [_serialize_container(c) for c in containers]

    def start(self, sandbox_id: str) -> dict[str, Any]:
        container = self._get_client().containers.get(sandbox_id)
        container.start()
        container.reload()
        return _serialize_container(container)

    def stop(self, sandbox_id: str) -> dict[str, Any]:
        container = self._get_client().containers.get(sandbox_id)
        container.stop()
        container.reload()
        return _serialize_container(container)

    def delete(self, sandbox_id: str) -> None:
        try:
            container = self._get_client().containers.get(sandbox_id)
        except Exception:
            return
        try:
            container.remove(force=True)
        except Exception:
            logger.warning(
                "docker container remove failed for %s", sandbox_id, exc_info=True
            )

    def set_labels(self, sandbox_id: str, labels: dict[str, str]) -> dict[str, Any]:
        """Update container labels.

        Docker does not support live label mutation; we recreate the container
        with merged labels preserving image, env, and command.
        """
        client = self._get_client()
        existing = client.containers.get(sandbox_id)
        existing.reload()
        attrs = existing.attrs or {}
        config = attrs.get("Config") or {}
        merged_labels = dict(config.get("Labels") or {})
        merged_labels.update(_normalize_dict(labels))

        new_image = config.get("Image")
        env = config.get("Env") or []
        command = config.get("Cmd") or ["sleep", "infinity"]
        name = (getattr(existing, "name", None) or attrs.get("Name") or "").lstrip("/")

        existing.stop()
        existing.remove(force=True)

        host_kwargs = host_config_kwargs()
        replacement = client.containers.create(
            image=new_image,
            name=name,
            command=command,
            detach=True,
            tty=False,
            environment=env,
            labels=merged_labels,
            **host_kwargs,
        )
        replacement.start()
        replacement.reload()
        return _serialize_container(replacement)

    # -- Preview / observability --------------------------------------------

    def get_signed_preview_url(self, sandbox_id: str, port: int) -> dict[str, Any]:
        return {
            "url": None,
            "reason": "docker provider has no signed preview URL",
        }

    def get_build_logs_url(self, sandbox_id: str) -> str | None:
        return None

    # -- Exec ----------------------------------------------------------------

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> RawExecResult:
        client = await self._get_async_client()

        def _run() -> tuple[int, bytes, bytes]:
            container = client.containers.get(sandbox_id)
            wrapped = command
            if cwd:
                wrapped = f"cd {shlex.quote(cwd)} && ({command})"
            exit_code, output = container.exec_run(
                cmd=["/bin/bash", "-lc", wrapped],
                demux=True,
                tty=False,
            )
            stdout_b: bytes
            stderr_b: bytes
            if isinstance(output, tuple) and len(output) == 2:
                stdout_b = output[0] or b""
                stderr_b = output[1] or b""
            else:
                stdout_b = output if isinstance(output, (bytes, bytearray)) else b""
                stderr_b = b""
            return int(exit_code or 0), bytes(stdout_b), bytes(stderr_b)

        if timeout is not None:
            exit_code, stdout_b, stderr_b = await asyncio.wait_for(
                asyncio.to_thread(_run), timeout=timeout
            )
        else:
            exit_code, stdout_b, stderr_b = await asyncio.to_thread(_run)

        return RawExecResult(
            success=exit_code == 0,
            exit_code=exit_code,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
        )

    # -- Context preparation -------------------------------------------------

    def context_preparer(self, sandbox_id: str) -> Any:
        from sandbox.provider.docker.exec_context import DockerContextPreparer

        return DockerContextPreparer(sandbox_id)


__all__ = ["DockerProviderAdapter"]
