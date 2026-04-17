from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from agents.registry import register_definition, unregister_definition
from agents.types import AgentDefinition
from external_trigger.runner import RunResult
from external_trigger.tc_note import (
    TC_NOTE_EDIT_PROMPT,
    TC_NOTE_TURN_PROMPT,
    _resolve_note_taker_prompt,
    build_tc_note_user_prompt,
    run_tc_note,
)
from external_trigger.snapshot_history import format_snapshot_history
from team.builtins import register_all
from tools.task_center.toolkit import PostNoteInput


def test_tc_note_prompts_reference_submit_task_note() -> None:
    prompts = (TC_NOTE_EDIT_PROMPT, TC_NOTE_TURN_PROMPT)

    for prompt in prompts:
        assert "submit_task_note" in prompt
        assert "post_note" not in prompt


def test_format_snapshot_history_structures_snapshot() -> None:
    rendered = format_snapshot_history(
        [
            {"role": "user", "content": "Fix parser.py"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I edited parser.py."},
                    {
                        "type": "tool_use",
                        "name": "daytona_edit_file",
                        "input": {"path": "parser.py"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "ok",
                        "is_error": False,
                    }
                ],
            },
        ]
    )

    assert rendered.startswith("## Snapshot History")
    assert "### Message 1: user" in rendered
    assert "```text\nFix parser.py\n```" in rendered
    assert "#### Tool call: daytona_edit_file" in rendered
    assert '"path": "parser.py"' in rendered
    assert "#### Tool result: toolu_1 (ok)" in rendered


def test_build_tc_note_user_prompt_appends_snapshot_history() -> None:
    prompt = build_tc_note_user_prompt(
        "Call submit_task_note now.",
        [{"role": "assistant", "content": "Still working"}],
    )

    assert prompt.startswith("Call submit_task_note now.")
    assert "## Snapshot History" in prompt
    assert "### Message 1: assistant" in prompt
    assert "Still working" in prompt


async def test_run_tc_note_sends_structured_snapshot_as_prompt(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return RunResult(
            tool_name="submit_task_note",
            tool_input={"content": "Noted", "paths": ["parser.py"]},
            validated=PostNoteInput(content="Noted", paths=["parser.py"]),
            turns_used=1,
        )

    monkeypatch.setattr("external_trigger.tc_note.run", fake_run)

    result = await run_tc_note(
        task_id="t1",
        agent_run_id="run-1",
        messages=[{"role": "user", "content": "Fix parser.py"}],
        prompt=TC_NOTE_TURN_PROMPT,
        trigger="turn",
        api_client=AsyncMock(),
    )

    assert result.content == "Noted"
    assert captured["messages"] == []
    assert "## Snapshot History" in str(captured["prompt"])
    assert "Fix parser.py" in str(captured["prompt"])


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
