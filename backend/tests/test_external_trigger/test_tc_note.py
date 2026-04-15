from __future__ import annotations

from types import SimpleNamespace

from agents.registry import register_definition, unregister_definition
from agents.types import AgentDefinition
from external_trigger.tc_note import (
    TC_NOTE_EDIT_PROMPT,
    TC_NOTE_TURN_PROMPT,
    _resolve_note_taker_prompt,
)
from team.builtins import register_all


def test_tc_note_prompts_reference_submit_task_note() -> None:
    prompts = (TC_NOTE_EDIT_PROMPT, TC_NOTE_TURN_PROMPT)

    for prompt in prompts:
        assert "submit_task_note" in prompt
        assert "post_note" not in prompt


def test_tc_note_uses_builtin_note_taker_prompt_when_available() -> None:
    register_all()

    prompt, model = _resolve_note_taker_prompt()

    assert "Convert a frozen task snapshot into a concise Task Center note." in prompt
    assert "Your only output is `submit_task_note(...)`." in prompt
    assert "# Identity" not in prompt
    assert "# Role Boundary" not in prompt
    assert model is None


def test_tc_note_prefers_team_roster_note_taker(monkeypatch) -> None:
    register_definition(
        AgentDefinition(
            name="custom_note_taker",
            description="custom team note taker",
            role="note_taker",
            system_prompt="Custom roster-selected note taker prompt.",
            model="test-model",
            include_skills=False,
        )
    )
    monkeypatch.setattr(
        "team.runtime.registry.get",
        lambda team_run_id: SimpleNamespace(
            id=team_run_id,
            roster={"task_center_note_taker": ["custom_note_taker"]},
        ),
    )

    try:
        prompt, model = _resolve_note_taker_prompt("team-run-1")
        assert prompt == "Custom roster-selected note taker prompt."
        assert model == "test-model"
    finally:
        unregister_definition("custom_note_taker")
