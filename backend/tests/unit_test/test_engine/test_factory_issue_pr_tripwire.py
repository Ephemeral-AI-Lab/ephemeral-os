"""AC #10 — tracked regression: issue/PR injection.

Plan v3.3 stripped ``build_runtime_context_message`` from
``engine.agent.factory._build_agent_system_prompt`` to keep per-agent
system prompts deterministic across re-spawns. As a side effect, the
content of ``.ephemeralos/issue.md`` and ``.ephemeralos/pr-comments.md``
no longer reaches any agent's system prompt — those evidence files are
now orphan content awaiting follow-up #4
(``entry_executor_issue_pr_via_recipe``).

This tripwire test pins that regression so it can't drift back in
silently. The test is intentionally narrowly-scoped: any agent profile
loaded from disk, fed through the production system-prompt builder,
must NOT contain either heading.

**Deletion criterion.** When follow-up #4 lands and the entry_executor
recipe begins wiring issue/PR evidence into its packet (NOT the system
prompt), delete this file. Until then, the test is the only surfaced
signal that issue/PR content has temporarily fallen off the wire.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents import AgentDefinition, AgentKind, load_agents_dir
from engine.agent import factory as runtime_agent


_ISSUE_HEADING = "# Issue Context"
_PR_HEADING = "# Pull Request Comments"


def _stub_runtime_base(monkeypatch, text: str = "runtime base") -> None:
    monkeypatch.setattr(
        runtime_agent,
        "build_runtime_system_prompt",
        lambda *_args, **_kwargs: text,
    )


@pytest.fixture
def main_profile_definitions():
    """Load production main-profile agent definitions from disk.

    Uses the real loader (which prepends ``_main_role_contract.md`` to
    seven main-role profiles) so the resulting system prompts mirror
    what production launches feed the model.
    """
    from pathlib import Path

    profile_dir = Path(__file__).resolve().parents[3] / "src" / "agents" / "profile" / "main"
    return load_agents_dir(profile_dir)


def test_main_profile_definitions_have_no_issue_pr_headings(main_profile_definitions):
    """Every main-profile agent_def.system_prompt must lack issue/PR headings."""
    for defn in main_profile_definitions:
        body = defn.system_prompt or ""
        assert _ISSUE_HEADING not in body, (
            f"{defn.name}: system_prompt contains {_ISSUE_HEADING!r}. "
            "Follow-up #4 should land before this tripwire is removed."
        )
        assert _PR_HEADING not in body, (
            f"{defn.name}: system_prompt contains {_PR_HEADING!r}. "
            "Follow-up #4 should land before this tripwire is removed."
        )


def test_factory_system_prompt_has_no_issue_pr_headings(monkeypatch, main_profile_definitions):
    """Running each profile through the real ``_build_agent_system_prompt``
    must not produce an issue/PR heading either — the factory path
    (runtime base + agent body) is the actual wire surface."""
    _stub_runtime_base(monkeypatch)
    for defn in main_profile_definitions:
        prompt = runtime_agent._build_agent_system_prompt(
            SimpleNamespace(cwd="/tmp"),
            defn,
            settings=None,
        )
        assert _ISSUE_HEADING not in prompt, (
            f"{defn.name}: factory produced an issue heading."
        )
        assert _PR_HEADING not in prompt, (
            f"{defn.name}: factory produced a PR heading."
        )


def test_factory_omits_issue_pr_even_with_files_on_disk(tmp_path, monkeypatch):
    """Even when ``.ephemeralos/issue.md`` and ``.ephemeralos/pr-comments.md``
    exist on disk under the runtime cwd, the factory must NOT inline them.
    Step 4 of v3.3 stripped ``build_runtime_context_message``; this test
    pins that the strip held.
    """
    issue_file = tmp_path / ".ephemeralos" / "issue.md"
    issue_file.parent.mkdir(parents=True)
    issue_file.write_text("Some issue body referencing bug-XYZ", encoding="utf-8")
    pr_file = tmp_path / ".ephemeralos" / "pr-comments.md"
    pr_file.write_text("PR reviewer comment about CI flake", encoding="utf-8")

    _stub_runtime_base(monkeypatch)
    defn = AgentDefinition(
        name="planner",
        description="planner",
        agent_kind=AgentKind.PLANNER,
        system_prompt="role body",
    )
    prompt = runtime_agent._build_agent_system_prompt(
        SimpleNamespace(cwd=str(tmp_path)),
        defn,
        settings=None,
    )
    assert _ISSUE_HEADING not in prompt
    assert _PR_HEADING not in prompt
    assert "bug-XYZ" not in prompt
    assert "CI flake" not in prompt
