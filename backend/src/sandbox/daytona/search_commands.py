"""Sandbox search command builders for file tools."""

from __future__ import annotations

import json
import shlex


def build_glob_command(*, root: str, pattern: str) -> str:
    patterns = [pattern]
    if pattern.startswith("**/"):
        patterns.append(pattern[3:])
    payload = json.dumps(list(dict.fromkeys(p for p in patterns if p)))
    script = """
import fnmatch
import json
import os
import subprocess
import sys

root = sys.argv[1]
patterns = json.loads(sys.argv[2])
matches = []

if not os.path.exists(root):
    print("")
    raise SystemExit(0)

command = [
    "find",
    root,
    "(",
    "-name", ".git",
    "-o", "-name", ".hg",
    "-o", "-name", ".svn",
    "-o", "-name", "__pycache__",
    "-o", "-name", ".pytest_cache",
    "-o", "-name", ".mypy_cache",
    "-o", "-name", ".ruff_cache",
    "-o", "-name", "node_modules",
    "-o", "-name", ".venv",
    "-o", "-name", "venv",
    "-o", "-name", "dist",
    "-o", "-name", "build",
    ")",
    "-prune",
    "-o",
    "-type",
    "f",
    "-print0",
]
proc = subprocess.Popen(
    command,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

stdout, stderr = proc.communicate()
if proc.returncode not in (0, None):
    sys.stderr.write(stderr.decode("utf-8", errors="replace"))
    raise SystemExit(proc.returncode)

for raw_path in stdout.split(b"\\0"):
    if not raw_path:
        continue
    full_path = raw_path.decode("utf-8", errors="replace")
    filename = os.path.basename(full_path)
    rel_path = os.path.relpath(full_path, root).replace(os.sep, "/")
    if not any(
        fnmatch.fnmatch(rel_path, item) or fnmatch.fnmatch(filename, item)
        for item in patterns
    ):
        continue
    matches.append(full_path)

print("\\n".join(matches))
    """
    return (
        f"python3 -c {shlex.quote(script)} "
        f"{shlex.quote(root)} {shlex.quote(payload)}"
    )


def build_grep_command(*, root: str, pattern: str) -> str:
    script = r"""
import json
import os
import pathlib
import subprocess
import sys

pattern = sys.argv[1]
root = pathlib.Path(sys.argv[2])

if not root.exists():
    print(json.dumps({"ok": False, "error": f"Path does not exist: {root}"}))
    sys.exit(1)

def _grep_supports(flag):
    probe = subprocess.run(
        ["grep", flag, "", os.devnull],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=False,
    )
    return probe.returncode in (0, 1)


regex_flag = "-P" if _grep_supports("-P") else "-E"
validator = subprocess.run(
    ["grep", regex_flag, "--", pattern, os.devnull],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    text=True,
)
if validator.returncode > 1:
    error = validator.stderr.strip() or "Invalid regex"
    print(json.dumps({"ok": False, "error": error}))
    sys.exit(2)

command = [
    "grep",
    "-RInH",
    "-Z",
    "-I",
    regex_flag,
    "--binary-files=without-match",
    "--exclude-dir=.git",
    "--exclude-dir=.hg",
    "--exclude-dir=.svn",
    "--exclude-dir=__pycache__",
    "--exclude-dir=.pytest_cache",
    "--exclude-dir=.mypy_cache",
    "--exclude-dir=.ruff_cache",
    "--exclude-dir=node_modules",
    "--exclude-dir=.venv",
    "--exclude-dir=venv",
    "--exclude-dir=dist",
    "--exclude-dir=build",
    "--",
    pattern,
    str(root),
]

proc = subprocess.Popen(
    command,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
matches = []

stdout, stderr = proc.communicate()
if proc.returncode not in (0, 1, None):
    error = stderr.decode("utf-8", errors="replace").strip() or "grep failed"
    print(json.dumps({"ok": False, "error": error}))
    sys.exit(proc.returncode)

for raw in stdout.splitlines():
    if b"\0" in raw:
        file_bytes, _, rest = raw.partition(b"\0")
        line_bytes, sep, content_bytes = rest.rstrip(b"\n").partition(b":")
    else:
        file_bytes, sep, rest = raw.rstrip(b"\n").partition(b":")
        if sep:
            line_bytes, sep, content_bytes = rest.partition(b":")
    if not sep:
        continue
    try:
        line_no = int(line_bytes.decode("ascii"))
    except ValueError:
        continue
    matches.append({
        "file": file_bytes.decode("utf-8", errors="replace"),
        "line": line_no,
        "content": content_bytes.decode("utf-8", errors="replace"),
    })

print(json.dumps({
    "ok": True,
    "matches": matches,
}))
"""
    return (
        f"python3 -c {shlex.quote(script)} "
        f"{shlex.quote(pattern)} {shlex.quote(root)}"
    )
