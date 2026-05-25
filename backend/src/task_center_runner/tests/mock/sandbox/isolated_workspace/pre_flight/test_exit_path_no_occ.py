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
_PIPELINE_PATH = _SRC_ROOT / "sandbox/isolated_workspace/pipeline.py"
_LIFECYCLE_PATH = _SRC_ROOT / "sandbox/isolated_workspace/_control_plane/handle_lifecycle.py"

# Anything that smells like an OCC publish call. Substring match — a literal
# in a method body is enough to fail.
_FORBIDDEN_TOKENS = (
    "apply_changeset",
    "commit_prepared",
    "commit_transaction",
    "CommitQueue",
    "apply_sync",  # the CommitQueue's sync entrypoint
)


def test_exit_and_teardown_methods_have_no_occ_calls() -> None:
    offenders = _method_token_offenders(
        _PIPELINE_PATH,
        class_names={"IsolatedPipeline"},
        method_names={"exit"},
    )
    offenders.extend(
        _method_token_offenders(
            _LIFECYCLE_PATH,
            class_names={"_WorkspaceHandleLifecycleMixin"},
            method_names={"_teardown", "_rollback_partial"},
        )
    )
    assert offenders == [], (
        f"exit/teardown/rollback paths must not invoke OCC publish primitives: {offenders}"
    )


def _method_token_offenders(
    path: Path,
    *,
    class_names: set[str],
    method_names: set[str],
) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    offenders: list[str] = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if cls.name not in class_names:
            continue
        for member in cls.body:
            if not isinstance(member, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if member.name not in method_names:
                continue
            body_segment = ast.get_source_segment(source, member) or ""
            for token in _FORBIDDEN_TOKENS:
                if re.search(rf"\b{re.escape(token)}\b", body_segment):
                    offenders.append(
                        f"{path.relative_to(_SRC_ROOT)}:{cls.name}.{member.name} references {token}"
                    )
    return offenders


def test_isolated_workspace_pipeline_modules_do_not_import_commit_queue() -> None:
    """A weaker, broader form of the above: the module never sees CommitQueue."""
    offenders: list[str] = []
    for path in (_PIPELINE_PATH, _LIFECYCLE_PATH):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "commit_queue" in alias.name.lower():
                        offenders.append(f"{path.relative_to(_SRC_ROOT)} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if "commit_queue" in target.lower():
                    offenders.append(f"{path.relative_to(_SRC_ROOT)} from {target} import ...")
                for alias in node.names:
                    if alias.name == "CommitQueue":
                        offenders.append(
                            f"{path.relative_to(_SRC_ROOT)} from {target} import CommitQueue"
                        )
    assert offenders == [], (
        f"isolated workspace pipeline modules must not import the OCC CommitQueue: {offenders}"
    )
