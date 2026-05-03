"""Tests for overlay runtime bootstrap registration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import sandbox.overlay.bootstrap as bootstrap
from sandbox.runtime import server
from sandbox.runtime import setup_orchestrator


def test_bootstrap_registers_overlay_setup_script() -> None:
    bootstrap.register()

    scripts = setup_orchestrator._REGISTRY.scripts  # type: ignore[attr-defined]

    assert any(
        script.name == "overlay"
        and script.package == "sandbox.overlay"
        and script.relative_path == "sandbox/overlay/setup.sh"
        for script in scripts
    )


def test_bootstrap_registers_overlay_handlers() -> None:
    saved = dict(server.OP_TABLE)
    server.OP_TABLE.clear()
    try:
        bootstrap.register()

        assert "overlay.run" in server.OP_TABLE
        assert "shell" in server.OP_TABLE
    finally:
        server.OP_TABLE.clear()
        server.OP_TABLE.update(saved)


def test_bootstrap_registration_is_idempotent() -> None:
    before = tuple(setup_orchestrator._REGISTRY.scripts)  # type: ignore[attr-defined]

    bootstrap.register()
    bootstrap.register()

    after = tuple(setup_orchestrator._REGISTRY.scripts)  # type: ignore[attr-defined]
    assert after == before


def test_setup_script_execution_is_idempotent() -> None:
    setup = Path(bootstrap.__file__).with_name("setup.sh")

    subprocess.run(["bash", str(setup)], check=True)
    subprocess.run(["bash", str(setup)], check=True)

    assert Path("/tmp/eos-shell-overlay").is_dir()
