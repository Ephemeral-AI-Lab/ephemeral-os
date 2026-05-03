"""Tests for OCC runtime bootstrap registration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import sandbox.occ.bootstrap as bootstrap
from sandbox.runtime import setup_orchestrator


def test_bootstrap_registers_occ_setup_script() -> None:
    bootstrap.register()

    scripts = setup_orchestrator._REGISTRY.scripts  # type: ignore[attr-defined]

    assert any(
        script.name == "occ"
        and script.package == "sandbox.occ"
        and script.relative_path == "sandbox/occ/setup.sh"
        for script in scripts
    )


def test_bootstrap_registration_is_idempotent() -> None:
    before = tuple(setup_orchestrator._REGISTRY.scripts)  # type: ignore[attr-defined]

    bootstrap.register()
    bootstrap.register()

    after = tuple(setup_orchestrator._REGISTRY.scripts)  # type: ignore[attr-defined]
    assert after == before


def test_setup_script_execution_is_idempotent(tmp_path: Path) -> None:
    setup = Path(bootstrap.__file__).with_name("setup.sh")
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}

    subprocess.run(["bash", str(setup)], check=True, env=env)
    subprocess.run(["bash", str(setup)], check=True, env=env)

    assert (tmp_path / ".cache" / "eos-ci").is_dir()
