"""Grep primitive for namespace-mounted workspaces."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping
from pathlib import Path

from sandbox._shared.models import GrepResult
from sandbox._shared.tool_primitives.file_ops import (
    is_regular_file_no_follow,
    read_bytes_no_follow,
    walk_dirs_no_follow,
)

_MAX_FILE_BYTES = 2 * 1024 * 1024


def compute(
    args: Mapping[str, object] | str,
    pattern: str | None = None,
    *,
    case_insensitive: bool = False,
) -> GrepResult:
    opts = _options(args, pattern=pattern, case_insensitive=case_insensitive)
    flags = re.MULTILINE
    if opts["case_insensitive"]:
        flags |= re.IGNORECASE
    if opts["multiline"]:
        flags |= re.DOTALL
    regex = re.compile(str(opts["pattern"]), flags)
    root = Path(str(opts["root"]))
    output_mode = str(opts["output_mode"])
    filenames: list[str] = []
    content_lines: list[str] = []
    num_matches = 0
    for path in sorted(_candidate_files(root)):
        rel = _display_path(path)
        glob_filter = opts["glob_filter"]
        if glob_filter and not fnmatch.fnmatch(rel, str(glob_filter)):
            continue
        try:
            data = read_bytes_no_follow(path)
            if len(data) > _MAX_FILE_BYTES:
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        matches = list(regex.finditer(text))
        if not matches:
            continue
        filenames.append(rel)
        num_matches += len(matches)
        if output_mode in {"content", "count"}:
            if output_mode == "count":
                content_lines.append(f"{rel}:{len(matches)}")
            else:
                content_lines.extend(_matching_lines(rel, text, regex, opts["line_numbers"]))
    filenames_tuple = tuple(filenames)
    content = "\n".join(content_lines)
    if content:
        content += "\n"
    return GrepResult(
        output_mode=output_mode,
        filenames=filenames_tuple,
        content=content,
        num_files=len(filenames_tuple),
        num_lines=len(content_lines) if output_mode == "content" else 0,
        num_matches=num_matches,
        applied_limit=None,
        applied_offset=0,
        truncated=False,
    )


def _options(
    args: Mapping[str, object] | str,
    *,
    pattern: str | None,
    case_insensitive: bool,
) -> dict[str, object]:
    if isinstance(args, Mapping):
        raw_root = args.get("path") or "."
        pattern = str(args.get("pattern") or "")
        case_insensitive = bool(args.get("case_insensitive", case_insensitive))
        return {
            "root": _absolute_no_escape(str(raw_root)),
            "pattern": pattern,
            "case_insensitive": case_insensitive,
            "glob_filter": args.get("glob_filter") or args.get("include_pattern"),
            "output_mode": str(args.get("output_mode") or "files_with_matches"),
            "line_numbers": bool(args.get("line_numbers", False)),
            "multiline": bool(args.get("multiline", False)),
        }
    if pattern is None:
        raise ValueError("pattern is required")
    return {
        "root": _absolute_no_escape(args),
        "pattern": pattern,
        "case_insensitive": case_insensitive,
        "glob_filter": None,
        "output_mode": "files_with_matches",
        "line_numbers": False,
        "multiline": False,
    }


def _matching_lines(rel: str, text: str, regex: re.Pattern[str], line_numbers: object) -> list[str]:
    lines: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if regex.search(line):
            prefix = f"{rel}:{lineno}:" if line_numbers else f"{rel}:"
            lines.append(prefix + line)
    return lines


def _candidate_files(root: Path) -> tuple[Path, ...]:
    if is_regular_file_no_follow(root):
        return (root,)
    return tuple(walk_dirs_no_follow(root))


def _display_path(path: Path) -> str:
    cwd = Path.cwd().resolve(strict=False)
    try:
        return path.resolve(strict=False).relative_to(cwd).as_posix()
    except ValueError:
        return path.as_posix()


def _absolute_no_escape(path: str) -> str:
    candidate = Path(str(path or "."))
    if not candidate.is_absolute():
        if ".." in candidate.parts:
            raise ValueError(f"path escapes workspace via '..': {path}")
        candidate = Path.cwd() / candidate
    return candidate.as_posix()


__all__ = ["compute"]
