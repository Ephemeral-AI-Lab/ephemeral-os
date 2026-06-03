"""Import fences for the host-only Python sandbox package."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path


BACKEND_SRC_ROOT = Path(__file__).resolve().parents[3] / "src"

REMOVED_DAEMON_SIDE_PACKAGES = (
    "sandbox.daemon",
    "sandbox.ephemeral_workspace",
    "sandbox.isolated_workspace",
    "sandbox.layer_stack",
    "sandbox.occ",
    "sandbox.overlay",
    "sandbox.shared",
)


def test_removed_daemon_side_packages_stay_absent() -> None:
    for module in REMOVED_DAEMON_SIDE_PACKAGES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            assert exc.name == module or module.startswith(f"{exc.name}.")
        else:
            raise AssertionError(f"{module} should not be importable")


def test_production_python_does_not_import_removed_daemon_side_packages() -> None:
    offenders: list[str] = []
    for path in _python_files(BACKEND_SRC_ROOT):
        for imported in _imports(path):
            if _matches_removed_package(imported):
                offenders.append(f"{path.relative_to(BACKEND_SRC_ROOT)} imports {imported}")

    assert offenders == []


def test_sandbox_package_has_only_host_provider_api_audit_and_shared_contracts() -> None:
    expected = {
        "__pycache__",
        "_contract_fixtures",
        "_shared",
        "api",
        "audit",
        "host",
        "provider",
    }
    actual = {
        path.name
        for path in (BACKEND_SRC_ROOT / "sandbox").iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    assert actual <= expected


def _matches_removed_package(imported: str) -> bool:
    return any(
        imported == package or imported.startswith(f"{package}.")
        for package in REMOVED_DAEMON_SIDE_PACKAGES
    )


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names
