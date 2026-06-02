"""Docker-only sandbox create / resume / setup primitives for SWE-EVO."""

from __future__ import annotations

import logging
from typing import Any

import sandbox.api as sandbox_api

from test_runner.benchmarks.sweevo._exec import (
    _exec,
    _wait_for_sandbox_exec_ready,
)
from test_runner.benchmarks.sweevo._snapshot import (
    resolve_sweevo_snapshot,
)
from test_runner.benchmarks.sweevo.models import (
    SWEEvoInstance,
    _CONDA_ACTIVATE,
    _DEFAULT_SANDBOX_SETUP_TIMEOUT,
    _DEFAULT_SWEEVO_INSTANCE_ID,
    _REPO_DIR,
    _has_explicit_sweevo_image_version,
    _normalize_sweevo_image_ref,
    _sweevo_sandbox_labels,
)

logger = logging.getLogger(__name__)


def _service() -> Any:
    return sandbox_api


def _find_existing_sandbox_by_name(service: Any, name: str) -> dict[str, Any] | None:
    """Return an existing sandbox record matching ``name`` if present."""
    for sandbox in service.list_sandboxes():
        if sandbox.get("name") == name:
            return sandbox
    return None


async def _create_sandbox(instance: SWEEvoInstance, name: str, repo_dir: str) -> str:
    service = _service()
    create_kwargs: dict[str, Any] = {}
    if _has_explicit_sweevo_image_version(instance.docker_image):
        snapshot = resolve_sweevo_snapshot(instance, register_snapshot=True)
        create_kwargs["snapshot"] = snapshot
    else:
        create_kwargs["image"] = _normalize_sweevo_image_ref(instance.docker_image)

    result = service.create_sandbox(
        name=name,
        language="python",
        labels=_sweevo_sandbox_labels(instance, repo_dir),
        **create_kwargs,
    )
    return str(result["id"])


async def _resume_sandbox(
    existing: dict[str, Any], name: str, instance: SWEEvoInstance, repo_dir: str,
) -> str:
    """Resume an existing container by name; recreate on unrecoverable status."""
    service = _service()
    status = (existing.get("status") or "").lower()
    sandbox_id = str(existing["id"])
    if status == "running":
        return sandbox_id
    if status in ("exited", "created", "paused"):
        service.start_sandbox(sandbox_id)
        return sandbox_id
    # "dead", "removing", "restarting" — recreate.
    logger.warning("Sandbox %s in unrecoverable status=%s; recreating", name, status)
    service.delete_sandbox(sandbox_id)
    return await _create_sandbox(instance, name, repo_dir)


async def setup_sweevo_sandbox(
    instance: SWEEvoInstance,
    sandbox_id: str,
    repo_dir: str = _REPO_DIR,
    *,
    install_lsp: bool = False,
    exec_ready_attempts: int = 6,
) -> str:
    """Prepare the sandbox by checking out the repo at the base commit.

    When *install_lsp* is true, the LSP catalog plugin is installed via
    :func:`sandbox.ephemeral_workspace.plugin.install.ensure_installed` after
    the workspace is rebuilt. Defaults to False so existing mock tiers and
    mock tests keep their pre-install-lsp behavior.
    """
    await _wait_for_sandbox_exec_ready(sandbox_id, attempts=exec_ready_attempts)
    await _exec(sandbox_id, _overlay_writable_root_setup_command())
    await _exec(sandbox_id, f"test -d {repo_dir} && test -d {repo_dir}/.git")
    await _exec(sandbox_id, f"{_CONDA_ACTIVATE} && python --version")
    # Retry runs may reuse the same named sandbox. Always restore the repo to
    # the base commit before reapplying the SWE-EVO test patch so failed edits
    # from earlier attempts do not contaminate the next run.
    await _exec(sandbox_id, f"cd {repo_dir} && git reset --hard HEAD 2>/dev/null")
    await _exec(sandbox_id, f"cd {repo_dir} && git clean -fd 2>/dev/null")
    await _exec(sandbox_id, f"cd {repo_dir} && git checkout -f {instance.base_commit} 2>/dev/null")
    await _exec(sandbox_id, f"cd {repo_dir} && git checkout -B sweevo-work {instance.base_commit} 2>/dev/null")
    await _exec(
        sandbox_id,
        f"{_CONDA_ACTIVATE} && cd {repo_dir} && pip install -e . -q 2>/dev/null || true",
        timeout=_DEFAULT_SANDBOX_SETUP_TIMEOUT,
    )
    await _rebuild_sweevo_workspace_base(sandbox_id, repo_dir)

    try:
        sandbox_info = sandbox_api.get_sandbox(sandbox_id)
        existing_labels = sandbox_info.get("labels", {})
        merged_labels = (
            {str(k): str(v) for k, v in existing_labels.items()}
            if isinstance(existing_labels, dict)
            else {}
        )
        merged_labels["project_dir"] = repo_dir
        sandbox_api.set_sandbox_labels(sandbox_id, merged_labels)
    except Exception as exc:
        logger.warning("Could not set project_dir label: %s", exc)

    if install_lsp:
        from sandbox.api.plugin_support import ensure_plugin_installed_and_loaded

        await ensure_plugin_installed_and_loaded(
            sandbox_id,
            plugin="lsp",
            workspace_root=repo_dir,
            timeout=120,
        )

    logger.info(
        "SWE-EVO sandbox %s ready: %s @ %s",
        sandbox_id,
        instance.repo,
        instance.base_commit[:12],
    )
    return repo_dir


def _overlay_writable_root_setup_command() -> str:
    """Ensure /eos/mount resolves to a writable non-overlay upper/work root."""
    return r"""
set -eu
mkdir -p /eos
if awk '$2 == "/eos" && $3 == "tmpfs" { found = 1 } END { exit found ? 0 : 1 }' /proc/mounts; then
    mkdir -p /eos/mount
elif awk '$2 == "/eos-mount-scratch" && $3 == "tmpfs" { found = 1 } END { exit found ? 0 : 1 }' /proc/mounts; then
    if [ -f /eos/daemon/runtime.pid ]; then
        pid=$(cat /eos/daemon/runtime.pid 2>/dev/null || true)
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
        fi
    fi
    rm -f /eos/daemon/runtime.sock /eos/daemon/runtime.pid
    mkdir -p /eos-mount-scratch/eos-sandbox-runtime
    if [ -e /eos/mount ] && [ ! -L /eos/mount ]; then
        rm -rf /eos/mount
    fi
    ln -sfn /eos-mount-scratch/eos-sandbox-runtime /eos/mount
else
    mkdir -p /eos/mount
fi
test -d /eos/mount && test -w /eos/mount
"""


async def _rebuild_sweevo_workspace_base(sandbox_id: str, repo_dir: str) -> None:
    """Rebind public-tool workspace truth after raw setup commands."""
    from sandbox.host.daemon_client import call_daemon_api, ensure_daemon_current
    from sandbox.host.runtime_bundle import ensure_runtime_uploaded

    await ensure_runtime_uploaded(sandbox_id)
    await ensure_daemon_current(sandbox_id)
    await call_daemon_api(
        sandbox_id,
        "api.build_workspace_base",
        {"workspace_root": repo_dir, "reset": True},
        timeout=240,
    )
    readiness = await call_daemon_api(
        sandbox_id,
        "api.runtime.ready",
        {},
        timeout=60,
    )
    if not (readiness.get("success") and readiness.get("ready")):
        raise RuntimeError(f"SWE-EVO sandbox runtime is not ready: {readiness!r}")


async def reset_sweevo_workspace(
    sandbox_id: str,
    *,
    install_lsp: bool = False,
) -> str:
    """Restore a reused SWE-EVO sandbox and rebuild the public-tool base."""
    from test_runner.benchmarks.sweevo.setup import load_sweevo_instance

    service = _service()
    sandbox_info = service.get_sandbox(sandbox_id)
    labels = sandbox_info.get("labels")
    label_map = (
        {str(key): str(value) for key, value in labels.items()}
        if isinstance(labels, dict)
        else {}
    )
    instance_id = label_map.get("sweevo_instance") or _DEFAULT_SWEEVO_INSTANCE_ID
    repo_dir = label_map.get("project_dir") or _REPO_DIR
    instance = load_sweevo_instance(instance_id=instance_id)
    return await setup_sweevo_sandbox(
        instance,
        sandbox_id,
        repo_dir,
        install_lsp=install_lsp,
    )


__all__ = [
    "_create_sandbox",
    "_find_existing_sandbox_by_name",
    "_resume_sandbox",
    "_service",
    "reset_sweevo_workspace",
    "setup_sweevo_sandbox",
]
