"""libcst codemod that rewrites ImportFrom and Import statements only.

Used by the sandbox-reframe waves to mechanically rewrite the ~209 import
sites across backend/. Strict invariants:

- Only ``cst.ImportFrom`` and ``cst.Import`` nodes are touched.
- ``cst.SimpleString``, ``cst.FormattedString``, ``cst.Name``,
  ``cst.Attribute`` outside an Import context are left untouched.
- The rewrite map is a prefix map: longest matching prefix wins so that
  ``sandbox.overlay`` -> ``sandbox.execution.overlay`` works even when
  ``sandbox`` -> ``sandbox`` is a no-op rewrite. A prefix only matches a
  whole dotted-segment boundary (so ``sandbox.overlayer`` does NOT match
  the prefix ``sandbox.overlay``).

Usage:
    codemod_sandbox_imports.py [--commit] --map JSON ROOTS...

Default mode is dry-run (no writes); pass ``--commit`` to apply rewrites in place.

Self-test:
    codemod_sandbox_imports.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

import libcst as cst


def _rewrite_dotted(dotted: str, rewrites: dict[str, str]) -> str | None:
    parts = dotted.split(".")
    best: tuple[int, str, str] | None = None
    for src, dst in rewrites.items():
        src_parts = src.split(".")
        if parts[: len(src_parts)] == src_parts:
            if best is None or len(src_parts) > best[0]:
                best = (len(src_parts), src, dst)
    if best is None:
        return None
    _, src, dst = best
    src_parts = src.split(".")
    new_parts = dst.split(".") + parts[len(src_parts):]
    return ".".join(new_parts)


def _attr_to_dotted(node: cst.BaseExpression) -> str | None:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        head = _attr_to_dotted(node.value)
        if head is None:
            return None
        return f"{head}.{node.attr.value}"
    return None


def _dotted_to_attr(dotted: str) -> cst.BaseExpression:
    parts = dotted.split(".")
    expr: cst.BaseExpression = cst.Name(parts[0])
    for p in parts[1:]:
        expr = cst.Attribute(value=expr, attr=cst.Name(p))
    return expr


class _ImportRewriter(cst.CSTTransformer):
    def __init__(self, rewrites: dict[str, str]) -> None:
        self.rewrites = rewrites
        self.changed = False

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        if updated_node.module is None:
            return updated_node
        dotted = _attr_to_dotted(updated_node.module)
        if dotted is None:
            return updated_node
        new = _rewrite_dotted(dotted, self.rewrites)
        if new is None or new == dotted:
            return updated_node
        self.changed = True
        return updated_node.with_changes(module=_dotted_to_attr(new))

    def leave_Import(
        self, original_node: cst.Import, updated_node: cst.Import
    ) -> cst.Import:
        new_names: list[cst.ImportAlias] = []
        local_changed = False
        for alias in updated_node.names:
            dotted = _attr_to_dotted(alias.name)
            if dotted is None:
                new_names.append(alias)
                continue
            new = _rewrite_dotted(dotted, self.rewrites)
            if new is None or new == dotted:
                new_names.append(alias)
                continue
            local_changed = True
            new_names.append(alias.with_changes(name=_dotted_to_attr(new)))
        if not local_changed:
            return updated_node
        self.changed = True
        return updated_node.with_changes(names=new_names)


def _walk_python_files(roots: Iterable[Path]) -> Iterator[Path]:
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if ".venv" in path.parts or "site-packages" in path.parts:
                continue
            yield path


def rewrite_file(path: Path, rewrites: dict[str, str], commit: bool) -> bool:
    src = path.read_text()
    try:
        module = cst.parse_module(src)
    except cst.ParserSyntaxError:
        return False
    rewriter = _ImportRewriter(rewrites)
    new_module = module.visit(rewriter)
    if not rewriter.changed:
        return False
    new_src = new_module.code
    if new_src == src:
        return False
    if commit:
        path.write_text(new_src)
    return True


def run_rewrite(
    rewrites: dict[str, str], roots: list[Path], commit: bool
) -> tuple[int, int]:
    seen = 0
    changed = 0
    for path in _walk_python_files(roots):
        seen += 1
        if rewrite_file(path, rewrites, commit):
            changed += 1
            print(("APPLY " if commit else "DRY   ") + str(path))
    return seen, changed


_SELF_TEST_SRC = '''\
"""docstring mentioning sandbox.command_exec for fun"""

from sandbox.command_exec import foo
from sandbox.command_exec.entrypoints import namespace_helper
from sandbox.overlay.factory import build
import sandbox.command_exec.policy

s = "sandbox.command_exec.X"
url = "https://example/sandbox.command_exec/x"

def f():
    from sandbox.overlay import cli
    return cli, foo, build, namespace_helper, sandbox.command_exec.policy
'''


def _self_test() -> int:
    rewrites = {
        "sandbox.overlay": "sandbox.execution.overlay",
        "sandbox.command_exec": "sandbox.execution",
    }
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fixture.py"
        p.write_text(_SELF_TEST_SRC)
        rewrite_file(p, rewrites, commit=True)
        out = p.read_text()
    expected_changed = [
        "from sandbox.execution import foo",
        "from sandbox.execution.entrypoints import namespace_helper",
        "from sandbox.execution.overlay.factory import build",
        "import sandbox.execution.policy",
        "from sandbox.execution.overlay import cli",
    ]
    # Strings/comments/docstrings MUST be preserved verbatim.
    expected_unchanged = [
        '"sandbox.command_exec.X"',
        '"https://example/sandbox.command_exec/x"',
        '"""docstring mentioning sandbox.command_exec for fun"""',
        # the identifier attribute access in a `return` is NOT an ImportFrom/Import
        # node — codemod must not touch it.
        "sandbox.command_exec.policy",
    ]
    ok = True
    for want in expected_changed:
        if want not in out:
            print(f"SELF-TEST FAIL (missing rewritten line): {want}")
            ok = False
    for keep in expected_unchanged:
        if keep not in out:
            print(f"SELF-TEST FAIL (unrelated text mangled): {keep}")
            ok = False
    if ok:
        print("SELF-TEST OK")
    else:
        print("SELF-TEST FAILED")
        print("--- actual output ---")
        print(out)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--map",
        type=str,
        default="{}",
        help="JSON dict of {old.dotted.prefix: new.dotted.prefix}",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Apply rewrites in place. Default is dry-run.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the built-in fixture self-test and exit.",
    )
    parser.add_argument("roots", nargs="*", help="Directories/files to scan")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        rewrites = json.loads(args.map)
    except json.JSONDecodeError as exc:
        print(f"--map must be JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(rewrites, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in rewrites.items()
    ):
        print("--map must be a dict of str -> str", file=sys.stderr)
        return 2

    roots = [Path(r) for r in args.roots] or [Path(".")]
    if not rewrites:
        print("no rewrites (--map is empty); nothing to do")
        return 0

    seen, changed = run_rewrite(rewrites, roots, commit=args.commit)
    mode = "APPLIED" if args.commit else "DRY-RUN"
    print(f"{mode}: scanned {seen} files, would change {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
