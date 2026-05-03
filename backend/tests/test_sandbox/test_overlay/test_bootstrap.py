"""Tests for overlay runtime bootstrap registration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import sandbox.overlay.bootstrap as bootstrap
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
