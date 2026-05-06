"""Helpers for public API tests over imported workspace-base stacks."""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from sandbox.api.tool import _runtime as runtime_mod

from .sandbox_fixture import SandboxHandle, WORKSPACE_ROOT


async def seed_imported_base(
    handle: SandboxHandle,
    files: Mapping[str, str],
    *,
    directories: tuple[str, ...] = (),
) -> dict[str, object]:
    """Seed raw `/testbed`, then import it as the runtime workspace base."""
    payload = {
        "files": dict(files),
        "directories": list(directories),
    }
    for path in payload["files"]:
        _validate_relative_path(str(path))
    for path in payload["directories"]:
        _validate_relative_path(str(path))

    script = r"""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
payload = json.loads(sys.argv[2])

for rel in payload["directories"]:
    (root / rel).mkdir(parents=True, exist_ok=True)
for rel, content in payload["files"].items():
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
"""
    result = await handle.raw_exec(
        handle.sandbox_id,
        "python3 -c {script} {root} {payload}".format(
            script=shlex.quote(script),
            root=shlex.quote(WORKSPACE_ROOT),
            payload=shlex.quote(json.dumps(payload, ensure_ascii=False)),
        ),
        timeout=60,
    )
    assert result.exit_code == 0, result.stderr or result.stdout

    built = await runtime_mod.call_runtime_api(
        handle.sandbox_id,
        "api.build_workspace_base",
        {"workspace_root": WORKSPACE_ROOT},
        timeout=180,
    )
    assert built.get("success") is True, built
    binding = built.get("binding")
    assert isinstance(binding, dict)
    assert binding.get("workspace_root") == WORKSPACE_ROOT
    assert binding.get("base_manifest_version") == 1
    return binding


def selected_runtime_ms(metric: Any) -> float:
    """Return the most relevant in-sandbox API runtime duration for a metric."""
    for key in (
        "api.read.total_s",
        "api.write.total_s",
        "api.edit.total_s",
        "api.shell.total_s",
        "api.shell.dispatch_total_s",
    ):
        value = metric.timings.get(key)
        if value is not None:
            return float(value) * 1000.0
    return float(metric.elapsed_ms)


def _validate_relative_path(path: str) -> None:
    posix = PurePosixPath(path)
    if posix.is_absolute() or ".." in posix.parts:
        raise ValueError(f"test fixture path must be workspace-relative: {path!r}")


__all__ = ["seed_imported_base", "selected_runtime_ms"]
