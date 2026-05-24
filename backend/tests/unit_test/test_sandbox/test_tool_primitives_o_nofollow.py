"""Static no-follow chokepoint checks for tool primitives."""

from __future__ import annotations

import ast
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3] / "src" / "sandbox" / "_shared" / "tool_primitives"


def test_file_primitives_use_file_ops_chokepoint() -> None:
    offenders: list[str] = []
    forbidden_calls = {
        "os.open",
        "globlib.glob",
        "Path.read_text",
        "Path.read_bytes",
        "Path.write_text",
        "Path.write_bytes",
        "Path.stat",
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
        "stat",
    }
    for path in (
        _ROOT / "read.py",
        _ROOT / "write.py",
        _ROOT / "edit.py",
        _ROOT / "grep.py",
        _ROOT / "glob.py",
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node.func)
            if call_name in forbidden_calls:
                offenders.append(f"{path.name}:{node.lineno}:{call_name}")
    assert offenders == []


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""
