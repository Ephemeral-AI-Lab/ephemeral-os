"""C2: ``exit`` and ``_teardown`` must not invoke OCC commit primitives.

The structural separation from OCC is that the isolated workspace's cleanup
path discards the tmpfs upperdir and releases the snapshot lease — it never
goes through ``apply_changeset`` / ``commit_prepared`` / ``CommitQueue.apply``.

This is a source-text scan because we want to catch the bug at the call site:
if a future refactor introduces a shared "cleanup helper" that flushes
upperdir to OCC, the helper call name appears textually in the exit path even
if the import-graph fence still passes (the helper might re-export under a
different name).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


_SRC_ROOT = Path(__file__).resolve().parents[7] / "src"
_MANAGER_PATH = _SRC_ROOT / "sandbox/isolated_workspace/manager.py"

# Anything that smells like an OCC publish call. Substring match — a literal
# in a method body is enough to fail.
_FORBIDDEN_TOKENS = (
    "apply_changeset",
    "commit_prepared",
    "commit_transaction",
    "CommitQueue",
    "apply_sync",  # the CommitQueue's sync entrypoint
)

_METHODS_UNDER_TEST = ("exit", "_teardown", "_rollback_partial")


def test_exit_and_teardown_methods_have_no_occ_calls() -> None:
    source = _MANAGER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_MANAGER_PATH))
    offenders: list[str] = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if cls.name != "IsolatedWorkspaceManager":
            continue
        for member in cls.body:
            if not isinstance(member, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if member.name not in _METHODS_UNDER_TEST:
                continue
            body_segment = ast.get_source_segment(source, member) or ""
            for token in _FORBIDDEN_TOKENS:
                if re.search(rf"\b{re.escape(token)}\b", body_segment):
                    offenders.append(
                        f"IsolatedWorkspaceManager.{member.name} references {token}"
                    )
    assert offenders == [], (
        "exit/teardown/rollback paths must not invoke OCC publish primitives: "
        f"{offenders}"
    )


def test_manager_module_does_not_import_commit_queue() -> None:
    """A weaker, broader form of the above: the module never sees CommitQueue."""
    source = _MANAGER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_MANAGER_PATH))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "commit_queue" in alias.name.lower():
                    offenders.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if "commit_queue" in target.lower():
                offenders.append(f"from {target} import ...")
            for alias in node.names:
                if alias.name == "CommitQueue":
                    offenders.append(f"from {target} import CommitQueue")
    assert offenders == [], (
        "service/isolated_workspace.py must not import the OCC CommitQueue: "
        f"{offenders}"
    )
