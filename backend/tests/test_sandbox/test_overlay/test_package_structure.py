"""Overlay package boundary tests for the Phase 02 snapshot layout."""

from __future__ import annotations

from pathlib import Path

import sandbox.occ
import sandbox.overlay
import sandbox.runtime.overlay_capture


def _overlay_root() -> Path:
    return Path(sandbox.overlay.__file__).resolve().parent


def _occ_root() -> Path:
    return Path(sandbox.occ.__file__).resolve().parent


def test_overlay_root_contains_only_target_layout_entries() -> None:
    expected = {
        "__init__.py",
        "capture",
        "client.py",
        "handlers",
        "namespace",
        "runner",
        "types.py",
    }

    actual = _source_entries(_overlay_root())

    assert actual == expected


def test_legacy_overlay_capture_runtime_is_outside_overlay_package() -> None:
    root = Path(sandbox.runtime.overlay_capture.__file__).resolve().parent
    expected = {
        "__init__.py",
        "bootstrap.py",
        "capture_engine.py",
        "capture_runtime_bundle.py",
        "command_codec.py",
        "config.py",
        "constants.py",
        "protocol.py",
        "run_artifacts.py",
        "runtime_execution.py",
        "setup.sh",
        "types.py",
        "wire.py",
    }

    actual = _source_entries(root)

    assert actual == expected


def test_overlay_shim_files_do_not_exist() -> None:
    forbidden = {
        "bootstrap.py",
        "capture_runner.py",
        "config.py",
        "daemon_local.py",
        "engine",
        "process_exec.py",
        "run.py",
        "runtime",
        "setup.sh",
        "support.py",
        "wire.py",
    }

    assert forbidden.isdisjoint(_source_entries(_overlay_root()))


def test_overlay_and_occ_do_not_import_each_other() -> None:
    overlay_hits = _grep_imports(_overlay_root(), "sandbox.occ")
    occ_hits = _grep_imports(_occ_root(), "sandbox.overlay")

    assert overlay_hits == []
    assert occ_hits == []


def test_old_code_intelligence_overlay_package_is_gone() -> None:
    assert not (_overlay_root().parent / "code_intelligence").exists()


def _grep_imports(root: Path, token: str) -> list[Path]:
    hits: list[Path] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if f"from {token}" in text or f"import {token}" in text:
            hits.append(path.relative_to(root))
    return hits


def _source_entries(root: Path) -> set[str]:
    entries: set[str] = set()
    for path in root.iterdir():
        if path.name in {"__pycache__", ".DS_Store"}:
            continue
        if path.is_dir() and not any(
            "__pycache__" not in child.parts and child.name != ".DS_Store"
            for child in path.rglob("*")
        ):
            continue
        entries.add(path.name)
    return entries
