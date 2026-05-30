"""Planner profile frontmatter and prompt assertions."""

from __future__ import annotations

from pathlib import Path

from agents import AgentRole, load_agents_dir

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PLANNER_DIR = BACKEND_ROOT / "src" / "agents" / "profile" / "main"


def _load_planner():
    loaded = load_agents_dir(PLANNER_DIR)
    by_name = {agent.name: agent for agent in loaded}
    assert "planner" in by_name
    assert "planner_closes_or_defers" not in by_name
    assert "planner_closes_goal" not in by_name
    return by_name["planner"]


def test_single_planner_definition_loads():
    planner = _load_planner()
    assert planner.role == AgentRole.PLANNER
    assert planner.context_recipe == "planner"


def test_main_profiles_do_not_declare_legacy_variants():
    for path in PLANNER_DIR.glob("*.md"):
        assert "\nvariants:" not in path.read_text(encoding="utf-8")


def test_planner_declares_full_terminal_surface_for_router_to_filter():
    planner = _load_planner()
    assert planner.terminals == [
        "submit_plan_closes_goal",
        "submit_plan_defers_goal",
    ]


def test_planner_no_longer_lists_recursive_partial_plan_trigger():
    planner = _load_planner()
    assert "recursive_partial_plan" not in planner.notification_triggers


def test_planner_names_valid_graph_agents():
    body = _load_planner().system_prompt or ""
    # `executor` is the only generator-capable agent name; repository-specific
    # names are explicitly rejected as invalid.
    assert "must be `executor`" in body
    assert "the only generator-capable agent" in body
    assert "invalid harness agent names" in body


def test_planner_treats_release_notes_as_code_repair_targets():
    body = _load_planner().system_prompt or ""
    assert "Code-repair benchmark framing" in body
    assert "treat that text as the behavior/code delta to implement" in body
    assert (
        "Do **not** plan to summarize, rewrite, or create a release-notes document"
        in body
    )
