#!/usr/bin/env python3
"""Generate a lightweight naming and reduction audit for target code."""

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


SOURCE_EXTENSIONS = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".rb",
    ".php",
    ".cs",
    ".cpp",
    ".cc",
    ".c",
    ".h",
    ".hpp",
}

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}

SUSPECT_TERMS = {
    "util": "vague utility bucket",
    "utils": "vague utility bucket",
    "helper": "vague helper bucket",
    "helpers": "vague helper bucket",
    "common": "umbrella shared bucket",
    "shared": "umbrella shared bucket",
    "misc": "miscellaneous bucket",
    "manager": "authority without responsibility",
    "handler": "generic event/action handler",
    "processor": "generic processing role",
    "service": "generic service role",
    "controller": "generic control role",
    "engine": "generic engine role",
    "data": "unstructured data name",
    "payload": "transport-shaped name may hide domain",
    "state": "overloaded lifecycle/state name",
    "status": "overloaded lifecycle/status name",
    "context": "overloaded context name",
    "result": "overloaded result name",
    "base": "base abstraction may hide ownership",
    "adapter": "adapter may be unnecessary indirection",
    "wrapper": "wrapper may be unnecessary indirection",
}


@dataclass(frozen=True)
class FileInfo:
    path: Path
    rel_path: str
    loc: int
    extension: str


@dataclass(frozen=True)
class SymbolInfo:
    file: str
    line: int
    kind: str
    name: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", help="Files or directories to audit")
    parser.add_argument("--repo", default=".", help="Repository root used for importer/test searches")
    parser.add_argument("--out", help="Write markdown report to this path")
    parser.add_argument("--max-files", type=int, default=400, help="Maximum source files to inspect")
    parser.add_argument("--max-rg-matches", type=int, default=12, help="Maximum importer matches per pattern")
    return parser.parse_args()


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in path.parts)


def iter_source_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix in SOURCE_EXTENSIONS else []
    files: list[Path] = []
    for root, dirs, filenames in os.walk(target):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        root_path = Path(root)
        if is_excluded(root_path):
            continue
        for filename in filenames:
            path = root_path / filename
            if path.suffix in SOURCE_EXTENSIONS and not is_excluded(path):
                files.append(path)
    return sorted(files)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def count_loc(path: Path) -> int:
    return sum(1 for line in read_text(path).splitlines() if line.strip())


def split_name(name: str) -> list[str]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return [part.lower() for part in re.split(r"[^A-Za-z0-9]+|_", normalized) if part]


def suspect_reason(name: str) -> str | None:
    parts = split_name(name)
    for part in parts:
        if part in SUSPECT_TERMS:
            return SUSPECT_TERMS[part]
    return None


def collect_files(targets: list[str], repo: Path, max_files: int) -> list[FileInfo]:
    seen: set[Path] = set()
    files: list[FileInfo] = []
    for raw_target in targets:
        target = Path(raw_target)
        if not target.is_absolute():
            target = (repo / target).resolve()
        for path in iter_source_files(target):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                rel_path = str(resolved.relative_to(repo))
            except ValueError:
                rel_path = str(resolved)
            files.append(FileInfo(resolved, rel_path, count_loc(resolved), resolved.suffix))
            if len(files) >= max_files:
                return files
    return files


def parse_python_symbols(file_info: FileInfo) -> list[SymbolInfo]:
    text = read_text(file_info.path)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    symbols: list[SymbolInfo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(SymbolInfo(file_info.rel_path, node.lineno, "class", node.name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(SymbolInfo(file_info.rel_path, node.lineno, "function", node.name))
    return symbols


TEXT_SYMBOL_PATTERNS = [
    ("class", re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("function", re.compile(r"\b(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("function", re.compile(r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\(")),
    ("type", re.compile(r"\b(?:interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("function", re.compile(r"\bfunc\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("function", re.compile(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)")),
]


def parse_text_symbols(file_info: FileInfo) -> list[SymbolInfo]:
    symbols: list[SymbolInfo] = []
    for line_number, line in enumerate(read_text(file_info.path).splitlines(), start=1):
        for kind, pattern in TEXT_SYMBOL_PATTERNS:
            match = pattern.search(line)
            if match:
                symbols.append(SymbolInfo(file_info.rel_path, line_number, kind, match.group(1)))
                break
    return symbols


def collect_symbols(files: list[FileInfo]) -> list[SymbolInfo]:
    symbols: list[SymbolInfo] = []
    for file_info in files:
        if file_info.extension in {".py", ".pyi"}:
            symbols.extend(parse_python_symbols(file_info))
        else:
            symbols.extend(parse_text_symbols(file_info))
    return symbols


def run_rg(repo: Path, pattern: str, max_matches: int) -> list[str]:
    rg = shutil.which("rg")
    if not rg:
        return []
    command = [
        rg,
        "-n",
        "--glob",
        "!.git",
        "--glob",
        "!node_modules",
        "--glob",
        "!.venv",
        "--glob",
        "!dist",
        "--glob",
        "!build",
        "--glob",
        "!__pycache__",
        "--",
        pattern,
        str(repo),
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return lines[:max_matches]


def importer_patterns(files: list[FileInfo]) -> list[str]:
    names: set[str] = set()
    for file_info in files:
        names.add(file_info.path.stem)
        if file_info.path.parent.name not in {"", "."}:
            names.add(file_info.path.parent.name)
    return sorted(name for name in names if len(name) > 2)[:20]


def test_patterns(files: list[FileInfo], symbols: list[SymbolInfo]) -> list[str]:
    names = {file_info.path.stem for file_info in files}
    names.update(symbol.name for symbol in symbols[:30])
    escaped = [re.escape(name) for name in sorted(names) if len(name) > 2]
    return escaped[:30]


def find_test_candidates(repo: Path, files: list[FileInfo], symbols: list[SymbolInfo]) -> list[str]:
    patterns = test_patterns(files, symbols)
    if not patterns:
        return []
    test_files: list[str] = []
    combined = re.compile("|".join(patterns), re.IGNORECASE)
    for root, dirs, filenames in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        root_path = Path(root)
        if is_excluded(root_path):
            continue
        if not re.search(r"test|spec", str(root_path), re.IGNORECASE):
            continue
        for filename in filenames:
            path = root_path / filename
            if path.suffix not in SOURCE_EXTENSIONS:
                continue
            rel_path = str(path.relative_to(repo))
            if combined.search(rel_path):
                test_files.append(rel_path)
    return sorted(set(test_files))[:40]


def format_report(repo: Path, files: list[FileInfo], symbols: list[SymbolInfo], max_rg_matches: int) -> str:
    lines: list[str] = []
    lines.append("# Refactor Audit")
    lines.append("")
    lines.append(f"Repository: `{repo}`")
    lines.append(f"Files inspected: {len(files)}")
    lines.append(f"Total nonblank LOC: {sum(file.loc for file in files)}")
    lines.append("")

    lines.append("## Largest Files")
    for file_info in sorted(files, key=lambda item: item.loc, reverse=True)[:20]:
        marker = " reduction-candidate" if file_info.loc >= 200 else ""
        lines.append(f"- `{file_info.rel_path}`: {file_info.loc} LOC{marker}")
    lines.append("")

    path_smells = []
    for file_info in files:
        for part in Path(file_info.rel_path).parts:
            reason = suspect_reason(Path(part).stem)
            if reason:
                path_smells.append((file_info.rel_path, part, reason))
                break
    lines.append("## Suspect Path Names")
    if path_smells:
        for rel_path, part, reason in path_smells[:40]:
            lines.append(f"- `{rel_path}` contains `{part}`: {reason}")
    else:
        lines.append("- None found by heuristic.")
    lines.append("")

    symbol_smells = []
    for symbol in symbols:
        reason = suspect_reason(symbol.name)
        if reason:
            symbol_smells.append((symbol, reason))
    lines.append("## Suspect Symbol Names")
    if symbol_smells:
        for symbol, reason in symbol_smells[:60]:
            lines.append(f"- `{symbol.name}` ({symbol.kind}) in `{symbol.file}:{symbol.line}`: {reason}")
    else:
        lines.append("- None found by heuristic.")
    lines.append("")

    lines.append("## Symbol Inventory")
    for symbol in symbols[:120]:
        lines.append(f"- `{symbol.name}` ({symbol.kind}) in `{symbol.file}:{symbol.line}`")
    if len(symbols) > 120:
        lines.append(f"- ... {len(symbols) - 120} more symbols omitted")
    lines.append("")

    lines.append("## Importer Hints")
    for pattern in importer_patterns(files):
        matches = run_rg(repo, rf"\b{re.escape(pattern)}\b", max_rg_matches)
        if matches:
            lines.append(f"- Pattern `{pattern}`")
            for match in matches:
                lines.append(f"  - `{match}`")
    lines.append("")

    lines.append("## Test Candidates")
    tests = find_test_candidates(repo, files, symbols)
    if tests:
        for test in tests:
            lines.append(f"- `{test}`")
    else:
        lines.append("- None found by heuristic. Use repo-specific test discovery.")
    lines.append("")

    lines.append("## Manual Follow-Up")
    lines.append("- Confirm public import paths before renaming modules.")
    lines.append("- Use LSP references or repo-specific search for exported symbols.")
    lines.append("- Choose the narrowest test command before editing.")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    repo = Path(args.repo).resolve()
    files = collect_files(args.targets, repo, args.max_files)
    symbols = collect_symbols(files)
    report = format_report(repo, files, symbols, args.max_rg_matches)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
