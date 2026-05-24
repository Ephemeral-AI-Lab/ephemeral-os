"""Static guards for Phase 2.5 background lifecycle boundaries."""

from __future__ import annotations

import ast
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3] / "src"


def test_pipeline_run_tool_call_has_no_background_registry_or_branch() -> None:
    forbidden = {
        "ShellJob",
        "ShellJobRegistry",
        "_background_jobs",
        "_session_jobs",
        "_dispatch_background_verb",
        "shell_launch",
        "shell_reap",
        "shell_poll",
        "shell_cancel",
    }
    for relpath, class_name in (
        ("sandbox/ephemeral_workspace/pipeline.py", "EphemeralPipeline"),
        ("sandbox/isolated_workspace/pipeline.py", "IsolatedPipeline"),
    ):
        source = (SRC_ROOT / relpath).read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = _method(tree, class_name, "run_tool_call")
        method_source = ast.get_source_segment(source, method) or ""
        assert "req.background" not in method_source
        assert "req.args" not in method_source or "background" not in method_source
        for name in forbidden:
            assert name not in method_source


def test_pipeline_body_has_no_background_branch() -> None:
    for relpath, class_name in (
        ("sandbox/ephemeral_workspace/pipeline.py", "EphemeralPipeline"),
        ("sandbox/isolated_workspace/pipeline.py", "IsolatedPipeline"),
    ):
        source = (SRC_ROOT / relpath).read_text(encoding="utf-8")
        tree = ast.parse(source)
        method_source = ast.get_source_segment(
            source,
            _method(tree, class_name, "run_tool_call"),
        ) or ""

        assert "background" not in method_source
        assert "ShellJob" not in method_source
        assert "_background_jobs" not in method_source


def _method(tree: ast.Module, class_name: str, method_name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == method_name
                ):
                    return item
    raise AssertionError(f"{class_name}.{method_name} not found")
