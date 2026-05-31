"""Generator submission prompt contract tests."""

from __future__ import annotations

from tools.submission.executor._prompt_guidance import (
    GENERATOR_SUBMISSION_CHOICE_GUIDANCE,
)
from tools.submission.executor.submit_generator_failure.prompt import (
    get_submit_generator_failure_description,
)
from tools.submission.executor.submit_generator_success.prompt import (
    get_submit_generator_success_description,
)
from tools.submission.executor.submit_workflow_handoff.prompt import (
    get_submit_workflow_handoff_description,
)


def test_generator_submission_prompts_share_three_way_guidance() -> None:
    success = get_submit_generator_success_description()
    failure = get_submit_generator_failure_description()
    handoff = get_submit_workflow_handoff_description()

    assert GENERATOR_SUBMISSION_CHOICE_GUIDANCE in success
    assert GENERATOR_SUBMISSION_CHOICE_GUIDANCE in failure
    assert GENERATOR_SUBMISSION_CHOICE_GUIDANCE in handoff
    assert "## Success vs Failure vs Handoff Decision" in success
    assert "Use `submit_generator_success` when:" in failure
    assert "Use `submit_generator_failure` when:" in handoff
    assert "Use `submit_workflow_handoff` when:" in success
    assert "Do not use handoff after editing has started" in handoff
