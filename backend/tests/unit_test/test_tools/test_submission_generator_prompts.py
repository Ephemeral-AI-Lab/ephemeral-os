"""Generator submission prompt contract tests."""

from __future__ import annotations

from tools.submission.generator._prompt_guidance import (
    GENERATOR_SUBMISSION_CHOICE_GUIDANCE,
)
from tools.submission.generator.submit_generator_outcome.prompt import (
    get_submit_generator_outcome_description,
)
from tools.submission.generator.submit_workflow_handoff.prompt import (
    get_submit_workflow_handoff_description,
)


def test_generator_submission_prompts_share_three_way_guidance() -> None:
    generator = get_submit_generator_outcome_description()
    handoff = get_submit_workflow_handoff_description()

    assert GENERATOR_SUBMISSION_CHOICE_GUIDANCE in generator
    assert GENERATOR_SUBMISSION_CHOICE_GUIDANCE in handoff
    assert "## Success vs Failure vs Handoff Decision" in generator
    assert '`status`: `"success"`' in generator
    assert '`"failed"`' in generator
    assert "Use `submit_workflow_handoff` when:" in generator
    assert "Do not use handoff after editing has started" in handoff
