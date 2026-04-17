"""Tests for tools.ci_toolkit.CIToolkit."""

from tools.ci_toolkit import CIToolkit


def test_ci_toolkit_registers_all_tools():
    ctx = type("Ctx", (), {"metadata": {}})()
    tk = CIToolkit.from_context(ctx)
    names = set(tk.tool_names())
    expected = {
        "ci_status",
        "ci_workspace_structure",
        "ci_query_symbol",
        "ci_diagnostics",
    }
    assert expected == names


def test_ci_toolkit_blocked_tools_handled_by_registry():
    """Verify that ToolRegistry.remove_tools works for role-based restrictions."""
    from tools.core.base import ToolRegistry

    ctx = type("Ctx", (), {"metadata": {}})()
    tk = CIToolkit.from_context(ctx)

    registry = ToolRegistry()
    registry.register_toolkit(tk)

    # Simulate planner blocklist
    registry.remove_tools(["ci_status"])
    remaining = {t.name for t in registry.list_tools()}

    assert "ci_status" not in remaining
    assert "ci_query_symbol" in remaining
    assert "ci_diagnostics" in remaining
