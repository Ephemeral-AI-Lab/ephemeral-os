"""Reducer submission prompt contract tests."""

from __future__ import annotations

from tools.submission.reducer._prompt_guidance import (
    REDUCTION_SUBMISSION_CHOICE_GUIDANCE,
)
from tools.submission.reducer.submit_reduction_failure.prompt import (
    get_submit_reduction_failure_description,
)
from tools.submission.reducer.submit_reduction_success.prompt import (
    get_submit_reduction_success_description,
)


def test_reducer_submission_prompts_share_success_failure_guidance() -> None:
    success = get_submit_reduction_success_description()
    failure = get_submit_reduction_failure_description()
    combined = f"{success}\n{failure}"

    assert REDUCTION_SUBMISSION_CHOICE_GUIDANCE in success
    assert REDUCTION_SUBMISSION_CHOICE_GUIDANCE in failure
    assert "finished the work in `<assigned_task>`" in success
    assert "summarizes the reducer result" in success
    assert "cannot finish the work in `<assigned_task>`" in failure
    assert "specific blocker or missing context" in failure
    assert "gate" not in combined.lower()
    assert "acceptance bar" not in combined
    assert "slice" not in combined.lower()
