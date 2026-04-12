"""Query-oriented CI tools — read-only code intelligence queries."""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
from pathlib import Path
from typing import Any

from code_intelligence.query_helpers import (
    _build_fallback_specs,
    _extract_match_name,
    _dedupe_matches,
    _parse_rg_matches,
    _build_reference_pattern,
    _parse_reference_matches,
    _python_fallback_query_symbols,
)
from code_intelligence.constants import SKIP_DIRECTORIES, SUPPORTED_EXTENSIONS
from tools.core.base import ToolExecutionContext, ToolResult
from tools.daytona_toolkit.ci_integration import (
    build_live_scope_packet,
    get_ci_service,
    get_daytona_sandbox,
    refresh_scope_baseline,
    scope_paths_for_write,
    resolve_daytona_path,
)
from team._path_utils import normalize_scope_paths
from tools.core.decorator import tool

logger = logging.getLogger(__name__)
_SYMBOL_FALLBACK_LIMIT = 100
_REFERENCE_FALLBACK_LIMIT = 100


def _normalize_workspace_path(path: str, *, workspace_root: str = "") -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    root = str(workspace_root or "").replace("\\", "/").rstrip("/")
    if root and normalized == root:
        return ""
    if root and normalized.startswith(root + "/"):
        normalized = normalized[len(root) + 1 :]
    return normalized.lstrip("./").strip("/")


def _indexed_workspace_paths(
    paths: list[str],
    *,
    workspace_root: str,
    path_prefix: str,
    max_depth: int,
) -> list[str]:
    normalized_prefix = _normalize_workspace_path(path_prefix, workspace_root=workspace_root)
    depth_limit = max(0, int(max_depth))
    rendered: list[str] = []
    for path in paths:
        rel_path = _normalize_workspace_path(path, workspace_root=workspace_root)
        if not rel_path:
            continue
        relative_to_prefix = rel_path
        if normalized_prefix:
            if rel_path == normalized_prefix:
                relative_to_prefix = ""
            elif rel_path.startswith(normalized_prefix + "/"):
                relative_to_prefix = rel_path[len(normalized_prefix) + 1 :]
            else:
                continue
        depth = (
            len([part for part in relative_to_prefix.split("/") if part])
            if relative_to_prefix
            else 0
        )
        if depth <= depth_limit:
            rendered.append(rel_path)
    return rendered


def _reference_result(
    references: list[dict[str, Any]],
    *,
    total_references: int | None = None,
) -> ToolResult:
    total = len(references) if total_references is None else int(total_references)
    return ToolResult(
        output=json.dumps(
            {
                "references": references[:50],
                "total_references": total,
                "truncated": total > 50,
            },
            indent=2,
        )
    )


def _maybe_warm_service(context: ToolExecutionContext, svc: Any, *, label: str) -> None:
    workspace_root = str(getattr(svc, "workspace_root", "") or "")
    has_remote_sandbox = get_daytona_sandbox(context) is not None
    should_skip_local_warmup = bool(
        has_remote_sandbox and workspace_root and not Path(workspace_root).is_dir()
    )
    if getattr(svc, "is_initialized", True) or should_skip_local_warmup:
        return
    try:
        svc.ensure_initialized(wait=True)
    except Exception:
        logger.debug("%s warmup failed", label, exc_info=True)


async def _exec_remote(
    context: ToolExecutionContext,
    command: str,
    *,
    timeout: int = 30,
    log_label: str,
) -> tuple[Any | None, str]:
    sandbox = get_daytona_sandbox(context)
    if sandbox is None:
        return None, ""
    try:
        response = await sandbox.process.exec(command, timeout=timeout)
    except Exception:
        logger.debug("%s failed", log_label, exc_info=True)
        return None, ""
    return response, (getattr(response, "result", "") or "").strip()


def _local_query_symbols(
    *,
    workspace_root: str,
    query: str,
    kind: str = "",
) -> list[dict[str, Any]] | None:
    """Search the local workspace when the symbol index is cold or incomplete."""
    root = Path(workspace_root)
    if not root.is_dir():
        return None

    collected: list[dict[str, Any]] = []
    for spec in _build_fallback_specs(query, kind=kind):
        try:
            response = subprocess.run(
                [
                    "rg",
                    "-n",
                    "--no-heading",
                    "--color",
                    "never",
                    "-e",
                    spec.pattern,
                    str(root),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError:
            return _python_fallback_query_symbols(root=root, query=query, kind=kind)
        except Exception:
            logger.debug("Local symbol query failed for %s", query, exc_info=True)
            continue
        if response.returncode not in (0, 1):
            continue
        if not response.stdout:
            continue
        collected.extend(_parse_rg_matches(response.stdout, query=query, kind=spec.kind))
        if len(collected) >= _SYMBOL_FALLBACK_LIMIT:
            break

    python_matches = _python_fallback_query_symbols(root=root, query=query, kind=kind)
    if python_matches:
        collected.extend(python_matches)
    deduped = _dedupe_matches(collected)
    return deduped or None


def _local_query_references(
    *,
    workspace_root: str,
    symbol: str,
    skip_file: str = "",
    skip_line: int = 0,
) -> list[dict[str, Any]] | None:
    root = Path(workspace_root)
    if not root.is_dir():
        return None

    pattern = _build_reference_pattern(symbol)
    if not pattern:
        return None
    try:
        response = subprocess.run(
            [
                "rg",
                "-n",
                "--no-heading",
                "--color",
                "never",
                "-e",
                pattern,
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return None
    except Exception:
        logger.debug("Local reference query failed for %s", symbol, exc_info=True)
        return None

    if response.returncode not in (0, 1) or not response.stdout:
        return None
    refs = _parse_reference_matches(
        response.stdout,
        symbol=symbol,
        skip_file=skip_file,
        skip_line=skip_line,
    )
    return refs or None


def _svc_or_error(context: ToolExecutionContext) -> tuple[Any | None, ToolResult | None]:
    """Get CI service or return an error ToolResult."""
    svc = get_ci_service(context)
    if svc is None:
        return None, ToolResult(
            output=json.dumps(
                {"status": "unavailable", "reason": "Code intelligence not configured"}
            ),
        )
    return svc, None


async def _remote_workspace_structure(
    context: ToolExecutionContext,
    *,
    path: str,
    max_depth: int,
) -> str | None:
    """List a sandbox-backed workspace when the local symbol index is cold."""
    target = resolve_daytona_path(path, context)
    command = f"find {shlex.quote(target)} -maxdepth {max(0, int(max_depth))} -print"
    response, output = await _exec_remote(
        context,
        command,
        log_label=f"Remote workspace listing for {target}",
    )
    if response is None:
        return None

    exit_code = getattr(response, "exit_code", 0)
    if exit_code != 0:
        logger.debug(
            "Remote workspace listing returned exit_code=%s for %s",
            exit_code,
            target,
        )
        return None
    if not output:
        return None

    lines = sorted(line for line in output.splitlines() if line.strip())
    if not lines:
        return None

    truncated = len(lines) > 500
    rendered = "\n".join(lines[:500])
    if truncated:
        rendered += "\n... (truncated at 500 files)"
    return rendered


async def _remote_query_symbols(
    context: ToolExecutionContext,
    *,
    query: str,
    kind: str = "",
) -> list[dict[str, Any]] | None:
    """Best-effort remote fallback for symbol search on cold starts."""
    target = resolve_daytona_path("", context)
    collected: list[dict[str, Any]] = []
    for spec in _build_fallback_specs(query, kind=kind):
        command = (
            f"rg -n --no-heading --color never -e {shlex.quote(spec.pattern)} {shlex.quote(target)}"
        )
        response, output = await _exec_remote(
            context,
            command,
            log_label=f"Remote symbol query for {query}",
        )
        if response is None:
            return None
        exit_code = getattr(response, "exit_code", 0)
        if exit_code not in (0, 1) or not output:
            continue
        collected.extend(_parse_rg_matches(output, query=query, kind=spec.kind))
        if len(collected) >= _SYMBOL_FALLBACK_LIMIT:
            break

    sandbox = get_daytona_sandbox(context)
    if sandbox is None:
        return None
    python_matches = await _remote_query_symbols_via_python(
        sandbox=sandbox,
        target=target,
        query=query,
        kind=kind,
    )
    if python_matches:
        collected.extend(python_matches)
    deduped = _dedupe_matches(collected)
    return deduped or None


async def _remote_query_references(
    context: ToolExecutionContext,
    *,
    symbol: str,
    skip_file: str = "",
    skip_line: int = 0,
) -> list[dict[str, Any]] | None:
    pattern = _build_reference_pattern(symbol)
    if not pattern:
        return None
    target = resolve_daytona_path("", context)
    command = f"rg -n --no-heading --color never -e {shlex.quote(pattern)} {shlex.quote(target)}"
    response, output = await _exec_remote(
        context,
        command,
        log_label=f"Remote reference query for {symbol}",
    )
    if response is None:
        return None

    exit_code = getattr(response, "exit_code", 0)
    if exit_code not in (0, 1) or not output:
        return None
    refs = _parse_reference_matches(
        output,
        symbol=symbol,
        skip_file=skip_file,
        skip_line=skip_line,
    )
    return refs or None


async def _remote_query_symbols_via_python(
    *,
    sandbox: Any,
    target: str,
    query: str,
    kind: str = "",
) -> list[dict[str, Any]] | None:
    """Portable remote fallback when ripgrep is unavailable in the sandbox."""
    specs = _build_fallback_specs(query, kind=kind)
    if not specs:
        return None

    payload = json.dumps(
        {
            "root": target,
            "patterns": [{"pattern": spec.pattern, "kind": spec.kind} for spec in specs],
            "skip_dirs": sorted(SKIP_DIRECTORIES),
            "extensions": sorted(SUPPORTED_EXTENSIONS),
            "limit": _SYMBOL_FALLBACK_LIMIT,
        }
    )
    script = """
import json
import os
import re
import sys

payload = json.loads(sys.argv[1])
root = payload["root"]
patterns = [(re.compile(item["pattern"]), item["kind"]) for item in payload["patterns"]]
skip_dirs = set(payload["skip_dirs"])
extensions = tuple(payload["extensions"])
limit = int(payload["limit"])
matches = []

for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [name for name in dirnames if name not in skip_dirs]
    for filename in filenames:
        if not filename.endswith(extensions):
            continue
        path = os.path.join(dirpath, filename)
        try:
            with open(path, encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, start=1):
                    for pattern, match_kind in patterns:
                        if pattern.search(line):
                            matches.append(
                                {
                                    "file": path,
                                    "line": lineno,
                                    "kind": match_kind,
                                    "snippet": line.strip()[:200],
                                }
                            )
                            break
                    if len(matches) >= limit:
                        break
        except Exception:
            continue
        if len(matches) >= limit:
            break
    if len(matches) >= limit:
        break

print(json.dumps(matches))
"""
    command = f"python -c {shlex.quote(script)} {shlex.quote(payload)}"
    try:
        response = await sandbox.process.exec(command, timeout=30)
    except Exception:
        logger.debug("Remote python symbol query failed for %s", query, exc_info=True)
        return None

    exit_code = getattr(response, "exit_code", 0)
    output = (getattr(response, "result", "") or "").strip()
    if exit_code != 0 or not output:
        return None
    try:
        raw_matches = json.loads(output)
    except Exception:
        logger.debug("Remote python symbol query produced invalid JSON for %s", query)
        return None

    collected: list[dict[str, Any]] = []
    for item in raw_matches:
        snippet = str(item.get("snippet") or "")
        matched_kind = str(item.get("kind") or "text_match")
        collected.append(
            {
                "name": _extract_match_name(snippet, query=query, kind=matched_kind),
                "kind": matched_kind,
                "file": str(item.get("file") or ""),
                "line": int(item.get("line") or 0),
                "signature": snippet[:200],
            }
        )
    deduped = _dedupe_matches(collected)
    return deduped or None


# -- CI Status ----------------------------------------------------------------


@tool(
    name="ci_status",
    description="Check code intelligence readiness: cache, index, LSP, and edit activity.",
    read_only=True,
)
async def ci_status(*, context: ToolExecutionContext) -> ToolResult:
    """Check code intelligence service readiness."""
    svc, err = _svc_or_error(context)
    if err:
        return err
    status = svc.status()
    return ToolResult(output=json.dumps(status, indent=2, default=str))


# -- Workspace Structure ------------------------------------------------------


@tool(
    name="ci_workspace_structure",
    description="List files and directories in the workspace, sorted by path.",
    read_only=True,
)
async def ci_workspace_structure(
    path: str = "",
    max_depth: int = 3,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """List workspace file structure.

    Args:
        path: Subdirectory to list (empty = workspace root)
        max_depth: Maximum directory depth

    Returns:
        output (str): File listing
    """
    svc, err = _svc_or_error(context)
    if err:
        return err
    si = svc.symbol_index
    if si is None:
        return ToolResult(output="Symbol index not available")
    workspace_root = str(getattr(svc, "workspace_root", "") or "")

    # Get indexed file paths
    from code_intelligence.analysis.symbol_index import SymbolIndex

    if isinstance(si, SymbolIndex):
        with si._lock:
            paths = sorted(si._symbols.keys())
    else:
        paths = []

    paths = _indexed_workspace_paths(
        paths,
        workspace_root=workspace_root,
        path_prefix=path,
        max_depth=max_depth,
    )

    # Limit output
    paths = paths[:500]
    output = "\n".join(paths)
    if len(paths) == 500:
        output += "\n... (truncated at 500 files)"

    if output:
        return ToolResult(output=output)

    remote_listing = await _remote_workspace_structure(
        context,
        path=path,
        max_depth=max_depth,
    )
    if remote_listing:
        return ToolResult(output=remote_listing)

    return ToolResult(output="No files indexed")


# -- Symbol Query -------------------------------------------------------------


@tool(
    name="ci_query_symbols",
    description="Find functions, classes, methods, and variables by name.",
    read_only=True,
)
async def ci_query_symbols(
    query: str,
    kind: str = "",
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Search for symbols by name.

    Args:
        query: Symbol name or partial name to search for
        kind: Filter by kind: function, class, method, variable

    Returns:
        symbols (list): Matching symbol entries
    """
    svc, err = _svc_or_error(context)
    if err:
        return err

    from code_intelligence.types import SymbolKind

    kind_filter = None
    if kind:
        try:
            kind_filter = SymbolKind(kind.lower())
        except ValueError:
            pass

    workspace_root = str(getattr(svc, "workspace_root", "") or "")
    _maybe_warm_service(context, svc, label="ci_query_symbols")

    agent_name = str((context.metadata or {}).get("agent_name") or "").strip()
    drop_text_matches = agent_name == "team_planner"

    results = svc.query_symbols(query)
    if kind_filter:
        results = [s for s in results if s.kind == kind_filter]
    if drop_text_matches:
        results = [
            s
            for s in results
            if (
                (getattr(getattr(s, "kind", None), "value", None) or str(getattr(s, "kind", "")))
                != "text_match"
            )
        ]

    if not results:
        fallback_matches: list[dict[str, Any]] = []
        local_matches = _local_query_symbols(
            workspace_root=workspace_root,
            query=query,
            kind=kind,
        )
        if local_matches:
            fallback_matches.extend(local_matches)
        remote_matches = await _remote_query_symbols(context, query=query, kind=kind)
        if remote_matches:
            fallback_matches.extend(remote_matches)
        fallback_matches = _dedupe_matches(fallback_matches)
        if drop_text_matches:
            fallback_matches = [
                match for match in fallback_matches if str(match.get("kind") or "") != "text_match"
            ]
        if fallback_matches:
            return ToolResult(output=json.dumps(fallback_matches, indent=2))
        return ToolResult(output=f"No symbols matching '{query}'")

    symbols = []
    for s in results[:100]:
        symbols.append(
            {
                "name": s.name,
                "kind": s.kind.value if hasattr(s.kind, "value") else str(s.kind),
                "file": s.file_path,
                "line": s.line,
                "signature": s.signature,
            }
        )

    return ToolResult(output=json.dumps(symbols, indent=2))


# -- Symbol References --------------------------------------------------------


@tool(
    name="ci_query_references",
    description="Find all usages of a symbol across the codebase.",
    read_only=True,
)
async def ci_query_references(
    file_path: str,
    symbol: str,
    line: int = 0,
    character: int = 0,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Find all references to a symbol across files.

    Args:
        file_path: File containing the symbol
        symbol: Symbol name to find references for
        line: Line number of the symbol
        character: Character offset

    Returns:
        refs (list): Reference locations
    """
    svc, err = _svc_or_error(context)
    if err:
        return err

    workspace_root = str(getattr(svc, "workspace_root", "") or "")
    _maybe_warm_service(context, svc, label="ci_query_references")

    results = svc.find_references(
        file_path,
        symbol,
        line,
        character,
    )
    if not results:
        fallback_refs: list[dict[str, Any]] = []
        local_refs = _local_query_references(
            workspace_root=workspace_root,
            symbol=symbol,
            skip_file=file_path,
            skip_line=line,
        )
        if local_refs:
            fallback_refs.extend(local_refs)
        remote_refs = await _remote_query_references(
            context,
            symbol=symbol,
            skip_file=file_path,
            skip_line=line,
        )
        if remote_refs:
            fallback_refs.extend(remote_refs)
        if fallback_refs:
            return _reference_result(
                fallback_refs,
                total_references=len(fallback_refs),
            )

        lsp = getattr(svc, "lsp_client", None)
        lsp_connected = bool(getattr(lsp, "connected", True)) if lsp is not None else True
        if not getattr(svc, "is_initialized", True) or not lsp_connected:
            return ToolResult(
                output=json.dumps(
                    {
                        "status": "cold",
                        "symbol": symbol,
                        "initialized": bool(getattr(svc, "is_initialized", False)),
                        "lsp_connected": lsp_connected,
                        "message": (
                            "Reference search returned no results while code intelligence "
                            "was still warming up or LSP was unavailable."
                        ),
                    },
                    indent=2,
                )
            )
        return ToolResult(output=f"No references found for '{symbol}'")

    refs = []
    for r in results[:50]:
        refs.append(
            {
                "file": r.file_path,
                "line": r.line,
                "text": r.text,
            }
        )

    return _reference_result(refs, total_references=len(results))


# -- Edit Hotspots ------------------------------------------------------------


@tool(
    name="ci_edit_hotspots",
    description="Return files that have been edited most frequently (conflict-prone).",
    read_only=True,
)
async def ci_edit_hotspots(
    limit: int = 10,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Find frequently edited / conflict-prone files.

    Args:
        limit: Max results

    Returns:
        items (list): Hotspot entries with file and edit_count
    """
    svc, err = _svc_or_error(context)
    if err:
        return err

    arbiter = svc.arbiter
    if arbiter is None:
        return ToolResult(output="Arbiter not available")

    hotspots = arbiter.hotspots(limit=limit)
    if not hotspots:
        return ToolResult(output="No edit hotspots recorded")

    items = [{"file": fp, "edit_count": count} for fp, count in hotspots]
    return ToolResult(output=json.dumps(items, indent=2))


# -- Recent Changes -----------------------------------------------------------


@tool(
    name="ci_recent_changes",
    description="List files changed in the last N seconds for change awareness.",
    read_only=True,
)
async def ci_recent_changes(
    seconds: float = 60.0,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """See files changed recently (by other agents or shell commands).

    Args:
        seconds: Look back window in seconds

    Returns:
        files (list): Recently changed file paths
    """
    svc, err = _svc_or_error(context)
    if err:
        return err

    arbiter = getattr(svc, "arbiter", None)
    if arbiter is None:
        return ToolResult(output="Arbiter not available")

    edits = arbiter.recent_edits(seconds=seconds)
    seen: set[str] = set()
    files: list[str] = []
    for e in edits:
        if e.file_path not in seen:
            seen.add(e.file_path)
            files.append(e.file_path)
    if not files:
        return ToolResult(output=f"No files changed in the last {seconds}s")

    return ToolResult(output=json.dumps(files, indent=2))
