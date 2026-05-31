"""Completeness tests for ``tools/_terminals/registry.TERMINAL_DESCRIPTORS``.

Asserts that:

* every terminal tool referenced by any ``agents/profile/main/*.md`` has a
  descriptor with non-empty ``selection_guidance`` and
  ``advisor_review_focus``.
* :func:`render_terminal_catalog` produces a bulleted catalog with the
  descriptor's selected field as the body and the terminal name as the
  bullet.
"""

from __future__ import annotations

import pathlib
import re


from tools._terminals.registry import (
    TERMINAL_DESCRIPTORS,
    render_terminal_catalog,
)


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[5]
_MAIN_PROFILE_DIR = _REPO_ROOT / "backend" / "src" / "agents" / "profile" / "main"


def _terminals_from_profile(md_path: pathlib.Path) -> list[str]:
    text = md_path.read_text(encoding="utf-8")
    front_match = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not front_match:
        return []
    front = front_match.group(1)
    in_terminals = False
    out: list[str] = []
    for line in front.splitlines():
        if line.startswith("terminals:"):
            in_terminals = True
            continue
        if in_terminals:
            stripped = line.strip()
            if stripped.startswith("- "):
                out.append(stripped[2:].strip().strip("\"'"))
            elif stripped and not stripped.startswith("- "):
                # New frontmatter key terminates the list.
                if ":" in stripped:
                    in_terminals = False
    return out


def test_every_main_profile_terminal_has_descriptor() -> None:
    referenced: set[str] = set()
    for md_path in _MAIN_PROFILE_DIR.glob("*.md"):
        referenced.update(_terminals_from_profile(md_path))
    # ``executor.md`` declares no terminals directly (it's a routing thin
    # entry-point) — make sure we still saw terminals from real profiles.
    assert referenced, "no terminals discovered across main profile MDs"
    missing = referenced - TERMINAL_DESCRIPTORS.keys()
    assert not missing, (
        f"profile-MD-referenced terminals missing from TERMINAL_DESCRIPTORS: "
        f"{sorted(missing)}. Add an entry in tools/_terminals/registry.py."
    )


def test_descriptors_have_non_empty_focus_fields() -> None:
    for name, descriptor in TERMINAL_DESCRIPTORS.items():
        assert descriptor.name == name
        assert descriptor.selection_guidance.strip(), (
            f"descriptor {name!r} has empty selection_guidance"
        )
        assert descriptor.advisor_review_focus.strip(), (
            f"descriptor {name!r} has empty advisor_review_focus"
        )


def test_reducer_terminal_descriptors_use_assigned_task_contract() -> None:
    descriptor = TERMINAL_DESCRIPTORS["submit_reducer_outcome"]
    combined = f"{descriptor.selection_guidance}\n{descriptor.advisor_review_focus}"

    assert 'status="success"' in descriptor.selection_guidance
    assert 'status="failed"' in descriptor.selection_guidance
    assert "assigned reducer work is actually complete" in descriptor.advisor_review_focus
    assert "prevents completion" in descriptor.advisor_review_focus
    assert "gate" not in combined.lower()
    assert "slice it gates" not in combined


def test_generator_handoff_descriptor_requires_pre_edit_decomposition() -> None:
    descriptor = TERMINAL_DESCRIPTORS["submit_workflow_handoff"]
    combined = f"{descriptor.selection_guidance}\n{descriptor.advisor_review_focus}"

    assert "you have not started edits" in descriptor.selection_guidance
    assert "planner decomposition is needed" in descriptor.selection_guidance
    assert "has not started edits" in descriptor.advisor_review_focus
    assert "why decomposition is needed" in descriptor.advisor_review_focus
    assert "bounded progress is made" not in combined


def test_planner_terminal_descriptors_use_iteration_outcome_contract() -> None:
    descriptor = TERMINAL_DESCRIPTORS["submit_planner_outcome"]
    combined = f"{descriptor.selection_guidance}\n{descriptor.advisor_review_focus}"

    assert "covers all current-iteration goal items" in descriptor.selection_guidance
    assert "leaves no remaining items" in descriptor.selection_guidance
    assert "deferred_goal_for_next_iteration" in descriptor.selection_guidance
    assert "explicitly listed" in descriptor.advisor_review_focus
    assert "slice" not in combined.lower()
    assert "partial scope" not in combined.lower()


def test_render_terminal_catalog_uses_selection_guidance() -> None:
    terminals = ["submit_generator_outcome", "submit_reducer_outcome"]
    catalog = render_terminal_catalog(terminals, focus="selection_guidance")
    for name in terminals:
        assert f"`{name}`" in catalog
    assert TERMINAL_DESCRIPTORS["submit_generator_outcome"].selection_guidance[:30] in catalog


def test_render_terminal_catalog_uses_advisor_review_focus() -> None:
    terminals = ["submit_generator_outcome", "submit_reducer_outcome"]
    catalog = render_terminal_catalog(terminals, focus="advisor_review_focus")
    assert TERMINAL_DESCRIPTORS["submit_generator_outcome"].advisor_review_focus[:30] in catalog


def test_render_terminal_catalog_uses_fallback_for_unknown_terminal() -> None:
    """Unknown terminals render a fallback bullet — the static completeness
    test is the strict drift guard, not the runtime renderer.
    """
    out = render_terminal_catalog(["definitely_not_a_terminal"], focus="selection_guidance")
    assert "definitely_not_a_terminal" in out
    assert "(no descriptor registered" in out


def test_render_terminal_catalog_returns_empty_for_no_terminals() -> None:
    assert render_terminal_catalog([], focus="selection_guidance") == ""
