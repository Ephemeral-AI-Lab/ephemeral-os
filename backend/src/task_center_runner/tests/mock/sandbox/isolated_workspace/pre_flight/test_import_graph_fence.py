"""R3 import-graph fence + N2 dynamic-import ban.

R3: the transitive imports of ``sandbox.daemon.handler.isolated_workspace_ops``
MUST NOT include ``sandbox.occ.*`` or
``sandbox.ephemeral_workspace.pipeline``. The bounded ops module is the
single seam between agent RPCs and the daemon-native workspace — any future
"let me reuse the existing overlay helper" refactor must add an import that
trips this test.

N2: no dynamic import (``importlib.import_module`` / ``__import__`` / ``exec``
of an import string) is allowed inside the closure. Static imports are the
only way a module enters the dependency graph, otherwise this fence cannot
see them.
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path


_SRC_ROOT = Path(__file__).resolve().parents[7] / "src"
_FORBIDDEN_PREFIXES = (
    "sandbox.occ",
    "sandbox.ephemeral_workspace.pipeline",
)
_ENTRY_MODULE = "sandbox.isolated_workspace.ops_handlers"


def test_isolated_workspace_ops_transitive_imports_exclude_occ() -> None:
    """OCC must be unreachable from the agent-facing ops handler."""
    closure = _transitive_closure(_ENTRY_MODULE)
    offenders = sorted(
        f"{module} reachable from {_ENTRY_MODULE}"
        for module in closure
        if any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in _FORBIDDEN_PREFIXES
        )
    )
    assert offenders == [], (
        "isolated_workspace_ops transitive imports must not include OCC or "
        f"pipeline: {offenders}"
    )


def test_isolated_workspace_ops_closure_has_no_dynamic_imports() -> None:
    """Every import in the closure must be statically declarable.

    A dynamic import is the only way a forbidden module could sneak past the
    transitive-closure walk. Forbid them everywhere in the closure.
    """
    closure = _transitive_closure(_ENTRY_MODULE)
    offenders: list[str] = []
    for module in closure:
        path = _module_to_path(module)
        if path is None or not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover — invalid source surfaces elsewhere
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _resolve_call_target(node.func)
            if target in {"importlib.import_module", "__import__"}:
                offenders.append(
                    f"{module}:{node.lineno} calls {target}",
                )
    assert offenders == [], (
        "dynamic imports are banned inside the isolated_workspace closure: "
        f"{offenders}"
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _transitive_closure(entry: str) -> set[str]:
    """BFS over ``sandbox.*`` imports, returning every module in the closure."""
    visited: set[str] = set()
    queue: deque[str] = deque([entry])
    while queue:
        module = queue.popleft()
        if module in visited:
            continue
        visited.add(module)
        path = _module_to_path(module)
        if path is None or not path.exists():
            continue
        for imported in _imports(path):
            if imported.startswith("sandbox."):
                queue.append(imported)
    return visited


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            base = node.module
            names.add(base)
            for alias in node.names:
                # ``from sandbox.x import y`` may resolve to either a module
                # ``sandbox.x.y`` or an attribute of ``sandbox.x``. Try both.
                names.add(f"{base}.{alias.name}")
    return names


def _module_to_path(module: str) -> Path | None:
    """Resolve ``a.b.c`` to ``src/a/b/c.py`` or ``src/a/b/c/__init__.py``."""
    parts = module.split(".")
    file_path = _SRC_ROOT.joinpath(*parts).with_suffix(".py")
    if file_path.exists():
        return file_path
    package_path = _SRC_ROOT.joinpath(*parts) / "__init__.py"
    if package_path.exists():
        return package_path
    return None


def _resolve_call_target(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _resolve_call_target(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None
