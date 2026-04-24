"""Tests for code intelligence tool exports."""

from tools.ci_toolkit import make_code_intelligence_tools


def test_ci_exports_all_tools():
    names = {tool.name for tool in make_code_intelligence_tools()}
    expected = {
        "ci_status",
        "ci_workspace_structure",
        "ci_query_symbol",
        "ci_diagnostics",
    }
    assert expected == names


def test_ci_tools_can_be_removed_from_registry():
    """Verify that ToolRegistry.remove_tools removes named tools."""
    from tools.core.base import ToolRegistry

    registry = ToolRegistry()
    registry.register_many(make_code_intelligence_tools())

    registry.remove_tools(["ci_status"])
    remaining = {t.name for t in registry.list_tools()}

    assert "ci_status" not in remaining
    assert "ci_query_symbol" in remaining
    assert "ci_diagnostics" in remaining
