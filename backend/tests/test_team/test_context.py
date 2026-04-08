"""Unit tests for team.context tiers."""

from __future__ import annotations

from team.context.project import ProjectContext


def test_project_context_append_only():
    pc = ProjectContext(goal="g", user_request="u")
    pc.add_rationale("r1")
    pc.add_note("n1")
    pc.add_rationale("")  # ignored
    assert pc.rationale_history == ["r1"]
    assert pc.notes == ["n1"]
    assert pc.to_dict()["goal"] == "g"
