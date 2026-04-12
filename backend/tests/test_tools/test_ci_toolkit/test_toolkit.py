"""Tests for tools.ci_toolkit.CIToolkit."""

from tools.ci_toolkit import CIToolkit


def test_ci_toolkit_registers_unified_query_tools():
    tk = CIToolkit()
    names = set(tk.tool_names())
    expected = {
        "ci_status",
        "ci_workspace_structure",
        "ci_query_symbols",
        "ci_query_references",
        "ci_hover",
        "ci_diagnostics",
        "ci_edit_hotspots",
        "ci_recent_changes",
        "ci_read_file",
    }
    assert expected.issubset(names)


def test_ci_toolkit_without_file_reads_keeps_unified_query_tools():
    tk = CIToolkit(include_file_reads=False)
    names = set(tk.tool_names())
    assert "ci_read_file" not in names
    assert "ci_hover" in names
    assert "ci_diagnostics" in names
    assert "ci_query_symbols" in names
    assert "ci_query_references" in names
