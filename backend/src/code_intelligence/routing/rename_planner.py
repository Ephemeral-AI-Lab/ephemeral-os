"""Semantic rename planning helpers for CodeIntelligenceService."""

from __future__ import annotations

import ast
import base64
import io
import json
import logging
import os
import shlex
import threading
import tokenize
import re
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

from tools.daytona_toolkit._daytona_utils import _extract_exit_code, _wrap_bash_command

from code_intelligence._async_bridge import run_sync
from code_intelligence.hashing import content_hash
from code_intelligence.tuning import CODE_INTELLIGENCE_TUNING
from code_intelligence.types import ReferenceInfo, SemanticFileChange, SemanticRenamePlan

logger = logging.getLogger(__name__)
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DEF_CLASS_NAME_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")


class _RenamePreviewSnapshot:
    """Reusable base data for dry-run rename previews."""

    def __init__(
        self,
        *,
        refs: tuple[ReferenceInfo, ...],
        base_by_path: dict[str, tuple[str, bool]],
        old_name: str,
    ) -> None:
        self.refs = refs
        self.base_by_path = base_by_path
        self.old_name = old_name


class _InflightRenamePreview:
    """One in-progress dry-run preview snapshot shared by callers."""

    def __init__(self, *, event: threading.Event) -> None:
        self.event = event


class _RenamePlanRequest:
    """Normalized internal semantic rename planning request."""

    def __init__(
        self,
        *,
        file_path: str,
        line: int,
        character: int,
        new_name: str,
    ) -> None:
        self.file_path = file_path
        self.line = line
        self.character = character
        self.new_name = new_name


class RenamePlanner:
    """Builds semantic rename plans and cached dry-run previews."""

    def __init__(
        self,
        *,
        workspace_root: str,
        sandbox: Any,
        content: Any,
        lsp_client: Any,
        arbiter: Any,
        symbol_index: Any,
    ) -> None:
        self.workspace_root = workspace_root
        self._sandbox = sandbox
        self._content = content
        self.lsp_client = lsp_client
        self.arbiter = arbiter
        self.symbol_index = symbol_index
        self._rename_preview_cache_lock = threading.Lock()
        self._rename_preview_cache: OrderedDict[
            tuple[str, int, int, int, int, int],
            _RenamePreviewSnapshot,
        ] = OrderedDict()
        self._rename_preview_inflight: dict[
            tuple[str, int, int, int, int, int],
            _InflightRenamePreview,
        ] = {}
        self._rename_preview_fast_fallbacks = 0

    def bind_sandbox(self, sandbox: Any) -> None:
        self._sandbox = sandbox

    @property
    def fast_fallbacks(self) -> int:
        return self._rename_preview_fast_fallbacks

    def rename_symbol_plan(
        self, file_path: str, line: int, character: int, new_name: str,
    ) -> SemanticRenamePlan:
        """Build a :class:`SemanticRenamePlan` for a semantic rename operation.

        For each affected file, capture current content so callers can render
        a dry-run preview or build one process-backed rename command.
        """
        try:
            fast_plan = self._preview_rename_symbol_plan_fast(
                file_path,
                int(line),
                int(character),
                new_name,
            )
        except Exception:
            logger.debug("fast rename plan failed for %s:%s", file_path, line, exc_info=True)
            fast_plan = None
        if fast_plan is not None:
            return fast_plan

        final_by_path = self.lsp_client.rename_symbol(
            file_path, int(line), int(character), new_name,
        )
        changes: list[SemanticFileChange] = []
        try:
            base_by_path = self._content.read_many(
                list(final_by_path.keys()),
                allow_missing=True,
            )
        except Exception:  # pragma: no cover - defensive I/O
            base_by_path = {}
        for path, final_content in final_by_path.items():
            base_content, existed = base_by_path.get(path, ("", False))
            # Missing files are skipped: Jedi would not have produced a
            # rewrite against a file it could not see.
            if not existed and not base_content:
                continue
            changes.append(
                SemanticFileChange(
                    file_path=path,
                    base_content=base_content,
                    base_hash=content_hash(base_content),
                    final_content=final_content,
                ),
            )
        return SemanticRenamePlan(
            new_name=new_name,
            origin=(file_path, int(line), int(character)),
            changes=tuple(changes),
        )

    def rename_symbol_plans_many(
        self,
        requests: Sequence[dict[str, Any]],
    ) -> list[SemanticRenamePlan]:
        """Build many rename plans with one LSP/Jedi backend call where possible."""
        normalized = [
            _RenamePlanRequest(
                file_path=str(req.get("file_path") or ""),
                line=int(req.get("line") or 0),
                character=int(req.get("character") or 0),
                new_name=str(req.get("new_name") or ""),
            )
            for req in requests
        ]
        if not normalized:
            return []
        if len(normalized) == 1:
            req = normalized[0]
            return [
                self.rename_symbol_plan(
                    req.file_path,
                    req.line,
                    req.character,
                    req.new_name,
                )
            ]

        same_file_plans = self._rename_symbol_plans_many_same_file_fast(normalized)
        if same_file_plans is not None:
            return same_file_plans

        try:
            fast_plans = self._rename_symbol_plans_many_fast(normalized)
        except Exception:
            logger.debug("fast batch rename plan failed", exc_info=True)
            fast_plans = None
        if fast_plans is not None:
            return fast_plans

        final_maps = self.lsp_client.rename_symbols(
            [
                (req.file_path, req.line, req.character, req.new_name)
                for req in normalized
            ]
        )
        all_paths: list[str] = []
        for final_by_path in final_maps:
            all_paths.extend(final_by_path)
        try:
            base_by_path = self._content.read_many(
                list(dict.fromkeys(all_paths)),
                allow_missing=True,
            )
        except Exception:  # pragma: no cover - defensive I/O
            base_by_path = {}

        plans: list[SemanticRenamePlan] = []
        for req, final_by_path in zip(normalized, final_maps, strict=True):
            changes: list[SemanticFileChange] = []
            for path, final_content in final_by_path.items():
                base_content, existed = base_by_path.get(path, ("", False))
                if not existed and not base_content:
                    continue
                changes.append(
                    SemanticFileChange(
                        file_path=path,
                        base_content=base_content,
                        base_hash=content_hash(base_content),
                        final_content=final_content,
                    ),
                )
            plans.append(
                SemanticRenamePlan(
                    new_name=req.new_name,
                    origin=(req.file_path, int(req.line), int(req.character)),
                    changes=tuple(changes),
                )
            )
        return plans

    def _rename_symbol_plans_many_same_file_fast(
        self,
        requests: Sequence[_RenamePlanRequest],
    ) -> list[SemanticRenamePlan] | None:
        origin_paths = [req.file_path for req in requests]
        base_by_path = self._content.read_many(
            list(dict.fromkeys(origin_paths)),
            allow_missing=True,
        )

        candidates: list[tuple[_RenamePlanRequest, str, str, str]] = []
        old_names: set[str] = set()
        for req in requests:
            base_content, existed = base_by_path.get(req.file_path, ("", False))
            if not existed:
                return None
            old_name = _identifier_at_position(
                base_content,
                int(req.line),
                int(req.character),
            )
            if not old_name:
                return None
            if old_name == req.new_name:
                candidates.append((req, old_name, base_content, base_content))
                continue
            final_content = _same_file_identifier_rename(
                base_content,
                old_name=old_name,
                new_name=req.new_name,
                origin_line=int(req.line),
            )
            if final_content is None or final_content == base_content:
                return None
            candidates.append((req, old_name, base_content, final_content))
            old_names.add(old_name)

        occurrences = self._workspace_identifier_occurrence_paths(old_names)
        if occurrences is None:
            return None
        for req, old_name, _, _ in candidates:
            paths = occurrences.get(old_name, set())
            if paths and paths != {req.file_path}:
                return None

        plans: list[SemanticRenamePlan] = []
        for req, _old_name, base_content, final_content in candidates:
            changes: tuple[SemanticFileChange, ...]
            if final_content == base_content:
                changes = ()
            else:
                changes = (
                    SemanticFileChange(
                        file_path=req.file_path,
                        base_content=base_content,
                        base_hash=content_hash(base_content),
                        final_content=final_content,
                    ),
                )
            plans.append(
                SemanticRenamePlan(
                    new_name=req.new_name,
                    origin=(req.file_path, int(req.line), int(req.character)),
                    changes=changes,
                )
            )
        return plans

    def _workspace_identifier_occurrence_paths(
        self,
        names: set[str],
    ) -> dict[str, set[str]] | None:
        if not names:
            return {}
        sandbox = getattr(self, "_sandbox", None)
        process = getattr(sandbox, "process", None)
        exec_fn = getattr(process, "exec", None) if process is not None else None
        if callable(exec_fn):
            return self._workspace_identifier_occurrence_paths_remote(names)
        return self._workspace_identifier_occurrence_paths_local(names)

    def _workspace_identifier_occurrence_paths_local(
        self,
        names: set[str],
    ) -> dict[str, set[str]] | None:
        root = os.path.abspath(self.workspace_root)
        results = {name: set() for name in names}
        file_count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name
                for name in dirnames
                if name not in {".git", "__pycache__", "node_modules", ".venv"}
            ]
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                file_count += 1
                if file_count > 1000:
                    return None
                path = os.path.join(dirpath, filename)
                try:
                    content = open(path, encoding="utf-8").read()
                except OSError:
                    continue
                for name in _identifier_names_in_content(content, names):
                    results[name].add(path)
        return results

    def _workspace_identifier_occurrence_paths_remote(
        self,
        names: set[str],
    ) -> dict[str, set[str]] | None:
        payload = base64.b64encode(json.dumps(sorted(names)).encode("utf-8")).decode("ascii")
        script = """
import base64
import json
import os
import re
import subprocess
import sys

root = os.path.abspath(sys.argv[1])
names = json.loads(base64.b64decode(sys.argv[2]).decode("utf-8"))
results = {name: [] for name in names}
for name in names:
    pattern = r"(^|[^[:alnum:]_])" + re.escape(name) + r"([^[:alnum:]_]|$)"
    proc = subprocess.run(
        [
            "grep",
            "-RIlE",
            "--include=*.py",
            "--exclude-dir=.git",
            "--exclude-dir=__pycache__",
            "--exclude-dir=node_modules",
            "--exclude-dir=.venv",
            pattern,
            root,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode not in (0, 1):
        print(json.dumps({"ok": False, "reason": proc.stderr[-500:]}))
        raise SystemExit(0)
    paths = [
        os.path.abspath(line)
        for line in proc.stdout.splitlines()
        if line.strip()
    ]
    results[name] = paths
print(json.dumps({"ok": True, "results": results}))
"""
        command = _wrap_bash_command(
            f"python3 -c {shlex.quote(script)} "
            f"{shlex.quote(self.workspace_root)} {shlex.quote(payload)}"
        )
        try:
            response = run_sync(
                getattr(self._sandbox.process, "exec")(command, timeout=30)
            )
        except Exception:
            return None
        stdout, exit_code = _extract_exit_code(
            str(getattr(response, "result", "") or ""),
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code not in (0, None):
            return None
        try:
            decoded = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            return None
        if not isinstance(decoded, dict) or not decoded.get("ok"):
            return None
        raw_results = decoded.get("results")
        if not isinstance(raw_results, dict):
            return None
        return {
            str(name): {str(path) for path in paths if isinstance(path, str)}
            for name, paths in raw_results.items()
            if isinstance(paths, list)
        }

    def _rename_symbol_plans_many_fast(
        self,
        requests: Sequence[_RenamePlanRequest],
    ) -> list[SemanticRenamePlan] | None:
        """Build rename plans from batched references and verified spans.

        Full Jedi rename computes final file contents directly, which is much
        heavier than the reference query we need for ordinary identifier
        replacements. This path preserves correctness by verifying every
        returned span before producing a plan; any uncertainty falls back to
        Jedi's rename engine.
        """
        references_many = getattr(self.lsp_client, "find_references_many", None)
        if not callable(references_many):
            return None
        refs_by_request = references_many(
            [(req.file_path, int(req.line), int(req.character)) for req in requests]
        )
        if len(refs_by_request) != len(requests):
            return None

        paths: list[str] = []
        for req, refs in zip(requests, refs_by_request, strict=True):
            paths.append(req.file_path)
            paths.extend(ref.file_path for ref in refs if ref.file_path)
        base_by_path = self._content.read_many(
            list(dict.fromkeys(paths)),
            allow_missing=True,
        )

        plans: list[SemanticRenamePlan] = []
        for req, refs in zip(requests, refs_by_request, strict=True):
            origin_content, origin_exists = base_by_path.get(req.file_path, ("", False))
            if not origin_exists:
                return None
            old_name = _identifier_at_position(
                origin_content,
                int(req.line),
                int(req.character),
            )
            if not old_name:
                return None
            if old_name == req.new_name:
                plans.append(
                    SemanticRenamePlan(
                        new_name=req.new_name,
                        origin=(req.file_path, int(req.line), int(req.character)),
                        changes=(),
                    )
                )
                continue
            if not refs:
                return None
            final_by_path = _apply_reference_replacements(
                refs=refs,
                base_by_path=base_by_path,
                old_name=old_name,
                new_name=req.new_name,
            )
            if final_by_path is None or not final_by_path:
                return None
            changes: list[SemanticFileChange] = []
            for path, final_content in final_by_path.items():
                base_content, existed = base_by_path.get(path, ("", False))
                if not existed:
                    return None
                changes.append(
                    SemanticFileChange(
                        file_path=path,
                        base_content=base_content,
                        base_hash=content_hash(base_content),
                        final_content=final_content,
                    )
                )
            plans.append(
                SemanticRenamePlan(
                    new_name=req.new_name,
                    origin=(req.file_path, int(req.line), int(req.character)),
                    changes=tuple(changes),
                )
            )
        return plans

    def _preview_rename_symbol_plan_fast(
        self,
        file_path: str,
        line: int,
        character: int,
        new_name: str,
    ) -> SemanticRenamePlan | None:
        snapshot = self._rename_preview_snapshot(file_path, line, character)
        if snapshot is None:
            return None
        if snapshot.old_name == new_name:
            return SemanticRenamePlan(
                new_name=new_name,
                origin=(file_path, int(line), int(character)),
                changes=(),
            )
        final_by_path = _apply_reference_replacements(
            refs=snapshot.refs,
            base_by_path=snapshot.base_by_path,
            old_name=snapshot.old_name,
            new_name=new_name,
        )
        if final_by_path is None:
            return None
        changes = []
        for path, final_content in final_by_path.items():
            base_content, existed = snapshot.base_by_path.get(path, ("", False))
            if not existed:
                continue
            changes.append(
                SemanticFileChange(
                    file_path=path,
                    base_content=base_content,
                    base_hash=content_hash(base_content),
                    final_content=final_content,
                ),
            )
        return SemanticRenamePlan(
            new_name=new_name,
            origin=(file_path, int(line), int(character)),
            changes=tuple(changes),
        )

    def _rename_preview_snapshot(
        self,
        file_path: str,
        line: int,
        character: int,
    ) -> _RenamePreviewSnapshot | None:
        key = (
            file_path,
            int(line),
            int(character),
            self.arbiter.generation,
            self.symbol_index.generation,
            id(getattr(self.lsp_client, "_sandbox", None)),
        )
        while True:
            with self._rename_preview_cache_lock:
                cached = self._rename_preview_cache.get(key)
                if cached is not None:
                    self._rename_preview_cache.move_to_end(key)
                    return cached
                inflight = self._rename_preview_inflight.get(key)
                if inflight is None:
                    inflight = _InflightRenamePreview(event=threading.Event())
                    self._rename_preview_inflight[key] = inflight
                    owner = True
                    break
                owner = False
            if not owner:
                inflight.event.wait()

        try:
            snapshot = self._build_rename_preview_snapshot(file_path, line, character)
            if snapshot is None:
                return None
            with self._rename_preview_cache_lock:
                self._rename_preview_cache[key] = snapshot
                self._rename_preview_cache.move_to_end(key)
                while (
                    len(self._rename_preview_cache)
                    > CODE_INTELLIGENCE_TUNING.rename_preview_cache_max
                ):
                    self._rename_preview_cache.popitem(last=False)
            return snapshot
        finally:
            with self._rename_preview_cache_lock:
                self._rename_preview_inflight.pop(key, None)
                inflight.event.set()

    def _build_rename_preview_snapshot(
        self,
        file_path: str,
        line: int,
        character: int,
    ) -> _RenamePreviewSnapshot | None:
        refs = tuple(self.lsp_client.find_references(file_path, line, character))
        if not refs:
            return None
        paths = [file_path, *(ref.file_path for ref in refs if ref.file_path)]
        base_by_path = self._content.read_many(paths, allow_missing=True)
        origin_content, origin_exists = base_by_path.get(file_path, ("", False))
        if not origin_exists:
            return None
        old_name = _identifier_at_position(origin_content, line, character)
        if not old_name:
            return None
        return _RenamePreviewSnapshot(
            refs=refs,
            base_by_path=base_by_path,
            old_name=old_name,
        )

    def cache_stats(self) -> dict[str, int]:
        with self._rename_preview_cache_lock:
            return {
                "entries": len(self._rename_preview_cache),
                "inflight_entries": len(self._rename_preview_inflight),
            }

    def clear_cache(self) -> None:
        with self._rename_preview_cache_lock:
            self._rename_preview_cache.clear()
            self._rename_preview_inflight.clear()

    def _rename_preview_cache_stats(self) -> dict[str, int]:
        with self._rename_preview_cache_lock:
            return {
                "size": len(self._rename_preview_cache),
                "inflight": len(self._rename_preview_inflight),
                "fast_fallbacks": self._rename_preview_fast_fallbacks,
            }

    def _clear_rename_preview_cache(self) -> None:
        with self._rename_preview_cache_lock:
            self._rename_preview_cache.clear()
            self._rename_preview_inflight.clear()


def _identifier_at_position(content: str, line: int, character: int) -> str:
    """Return the identifier at or immediately after a 1-indexed position."""
    lines = content.splitlines()
    if line < 1 or line > len(lines):
        return ""
    text = lines[line - 1]
    match = _DEF_CLASS_NAME_RE.match(text)
    if match and character <= match.end(1):
        return match.group(1)
    bounds = _identifier_bounds_near(text, character)
    if bounds is None:
        return ""
    start, end = bounds
    return text[start:end]


def _same_file_identifier_rename(
    content: str,
    *,
    old_name: str,
    new_name: str,
    origin_line: int,
) -> str | None:
    """Rename a same-file module symbol without invoking Jedi.

    The guard rejects files with another binding for the old name. Callers
    separately verify that no identifier occurrence exists outside this file.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    if not _has_single_origin_binding(tree, old_name=old_name, origin_line=origin_line):
        return None
    replacements: list[tuple[int, int, int, int]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(content).readline)
        for token in tokens:
            if token.type == tokenize.NAME and token.string == old_name:
                replacements.append((
                    token.start[0],
                    token.start[1],
                    token.end[0],
                    token.end[1],
                ))
    except tokenize.TokenError:
        return None
    if not replacements:
        return None
    return _replace_token_spans(content, replacements, new_name)


def _has_single_origin_binding(
    tree: ast.AST,
    *,
    old_name: str,
    origin_line: int,
) -> bool:
    origin_count = 0
    invalid = False

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nonlocal origin_count, invalid
            if node.name == old_name:
                if int(getattr(node, "lineno", 0) or 0) == origin_line:
                    origin_count += 1
                else:
                    invalid = True
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(node)  # type: ignore[arg-type]

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            nonlocal origin_count, invalid
            if node.name == old_name:
                if int(getattr(node, "lineno", 0) or 0) == origin_line:
                    origin_count += 1
                else:
                    invalid = True
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            nonlocal invalid
            if node.id == old_name and isinstance(node.ctx, (ast.Store, ast.Del)):
                invalid = True

        def visit_arg(self, node: ast.arg) -> None:
            nonlocal invalid
            if node.arg == old_name:
                invalid = True

        def visit_alias(self, node: ast.alias) -> None:
            nonlocal invalid
            bound_name = node.asname or node.name.rpartition(".")[2]
            if bound_name == old_name:
                invalid = True

        def visit_Global(self, node: ast.Global) -> None:
            nonlocal invalid
            if old_name in node.names:
                invalid = True

        def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
            nonlocal invalid
            if old_name in node.names:
                invalid = True

    Visitor().visit(tree)
    return origin_count == 1 and not invalid


def _identifier_names_in_content(content: str, names: set[str]) -> set[str]:
    found: set[str] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(content).readline)
        for token in tokens:
            if token.type == tokenize.NAME and token.string in names:
                found.add(token.string)
    except tokenize.TokenError:
        return set()
    return found


def _replace_token_spans(
    content: str,
    spans: Sequence[tuple[int, int, int, int]],
    replacement: str,
) -> str:
    lines = content.splitlines(keepends=True)
    for start_line, start_col, end_line, end_col in sorted(spans, reverse=True):
        if start_line != end_line or start_line < 1 or start_line > len(lines):
            continue
        text = lines[start_line - 1]
        lines[start_line - 1] = text[:start_col] + replacement + text[end_col:]
    return "".join(lines)


def _identifier_bounds_near(text: str, character: int) -> tuple[int, int] | None:
    if not text:
        return None
    pos = max(0, min(int(character), len(text)))
    if pos < len(text) and _is_identifier_char(text[pos]):
        start = pos
        end = pos
    elif pos > 0 and _is_identifier_char(text[pos - 1]):
        start = pos - 1
        end = pos - 1
    else:
        match = _IDENTIFIER_RE.search(text, pos)
        if match is None:
            return None
        return match.start(), match.end()
    while start > 0 and _is_identifier_char(text[start - 1]):
        start -= 1
    while end < len(text) and _is_identifier_char(text[end]):
        end += 1
    return start, end


def _apply_reference_replacements(
    *,
    refs: Sequence[ReferenceInfo],
    base_by_path: dict[str, tuple[str, bool]],
    old_name: str,
    new_name: str,
) -> dict[str, str] | None:
    """Apply verified identifier-span replacements for LSP references.

    Returns ``None`` when any reference does not point exactly at the
    expected identifier. Callers can then fall back to Jedi's full rename.
    """
    grouped: dict[str, set[tuple[int, int]]] = {}
    for ref in refs:
        if not ref.file_path:
            continue
        grouped.setdefault(ref.file_path, set()).add((int(ref.line), int(ref.character)))

    final_by_path: dict[str, str] = {}
    for path, positions in grouped.items():
        base_content, existed = base_by_path.get(path, ("", False))
        if not existed:
            return None
        lines = base_content.splitlines(keepends=True)
        changed = False
        for line, column in sorted(positions, reverse=True):
            if line < 1 or line > len(lines) or column < 0:
                return None
            text = lines[line - 1]
            end = column + len(old_name)
            if text[column:end] != old_name:
                return None
            if column > 0 and _is_identifier_char(text[column - 1]):
                return None
            if end < len(text) and _is_identifier_char(text[end]):
                return None
            lines[line - 1] = text[:column] + new_name + text[end:]
            changed = True
        if changed:
            final_content = "".join(lines)
            if final_content != base_content:
                final_by_path[path] = final_content
    return final_by_path


def _is_identifier_char(char: str) -> bool:
    return char == "_" or char.isalnum()
