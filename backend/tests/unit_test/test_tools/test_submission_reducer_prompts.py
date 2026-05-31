"""Reducer submission prompt contract tests."""

from __future__ import annotations

from tools.submission.reducer._prompt_guidance import (
    REDUCTION_SUBMISSION_CHOICE_GUIDANCE,
)
from tools.submission.reducer.submit_reducer_outcome.prompt import (
    get_submit_reducer_outcome_description,
)


def test_reducer_submission_prompts_share_success_failure_guidance() -> None:
    combined = get_submit_reducer_outcome_description()

    assert REDUCTION_SUBMISSION_CHOICE_GUIDANCE in combined
    assert '`status`: `"success"`' in combined
    assert '`"failed"`' in combined
    assert "concrete blocker/missing context" in combined
    assert "gate" not in combined.lower()
    assert "acceptance bar" not in combined
    assert "slice" not in combined.lower()
