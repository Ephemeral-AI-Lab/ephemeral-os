from __future__ import annotations

from external_trigger.tc_note import TC_NOTE_EDIT_PROMPT, TC_NOTE_TURN_PROMPT


def test_tc_note_prompts_reference_submit_task_note() -> None:
    prompts = (TC_NOTE_EDIT_PROMPT, TC_NOTE_TURN_PROMPT)

    for prompt in prompts:
        assert "submit_task_note" in prompt
        assert "post_note" not in prompt
