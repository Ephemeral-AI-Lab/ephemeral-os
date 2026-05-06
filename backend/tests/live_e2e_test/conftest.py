"""pytest entry for the live_e2e_test suites.

- Opt-in by directory: ``pyproject.toml``'s ``norecursedirs`` keeps the
  default ``pytest backend/tests`` invocation from walking into this
  package. Run with ``pytest backend/tests/live_e2e_test``.
- Re-exports the shared fixtures from ``sandbox/_harness/sandbox_fixture.py``.
- Enforces the sandbox import fence across the suite: live tests may use the
  public sandbox API or direct in-sandbox probes, but must not import
  ``sandbox.layer_stack``, ``sandbox.overlay``, or ``sandbox.occ`` directly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .sandbox._harness.sandbox_fixture import (  # noqa: F401
    integrated_sandbox,
    live_sandbox,
    native_sandbox,
    overlay_sandbox,
    workspace_base_sandbox,
)


SUITE_ROOT = Path(__file__).resolve().parent

_FORBIDDEN_SANDBOX_INTERNALS = (
    "sandbox.layer_stack",
    "sandbox.overlay",
    "sandbox.occ",
)


pytestmark = [pytest.mark.live]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: marks live Daytona-backed end-to-end tests (opt-in via env)",
    )


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _violations(imports: set[str], forbidden: tuple[str, ...]) -> list[str]:
    bad: list[str] = []
    for imported in imports:
        for prefix in forbidden:
            if imported == prefix or imported.startswith(f"{prefix}."):
                bad.append(imported)
                break
    return bad


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Fail when live-suite files import sandbox internals directly."""
    del config
    checked = _suite_python_files(items)
    for module_path in checked:
        try:
            relative = module_path.relative_to(SUITE_ROOT)
        except ValueError:
            continue
        imports = _module_imports(module_path)
        offences = _violations(imports, _FORBIDDEN_SANDBOX_INTERNALS)
        if offences:
            reason = (
                f"import-fence violation in {relative}: "
                f"forbidden imports {sorted(set(offences))}"
            )
            raise pytest.UsageError(reason)


def _suite_python_files(items: list[pytest.Item]) -> tuple[Path, ...]:
    collected = {
        Path(getattr(item, "fspath", item.path))  # type: ignore[arg-type]
        for item in items
    }
    suite_files = {
        path
        for path in SUITE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    return tuple(sorted(collected | suite_files))
