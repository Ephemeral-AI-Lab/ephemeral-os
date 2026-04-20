"""Tests for tools.submission.toolkit."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tools.submission.toolkit import SubmitTaskSummaryTool


def test_submit_task_summary_rejects_whitespace_only_content():
    with pytest.raises(ValidationError, match="content must contain non-whitespace text"):
        SubmitTaskSummaryTool.input_model(type="success", content=" \n\t")


def test_submit_task_summary_schema_requests_evidence_rich_content():
    schema = SubmitTaskSummaryTool().to_api_schema()
    content_desc = schema["input_schema"]["properties"]["content"]["description"]

    assert "Evidence-rich terminal summary" in content_desc
    assert "verification commands and outcomes" in content_desc
    assert "affected paths or owners" in content_desc
