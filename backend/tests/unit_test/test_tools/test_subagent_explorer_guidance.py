"""Coverage for the explorer subagent launch prompt."""

from __future__ import annotations

import inspect

from tools.subagent.explorer_guidance import (
    EXPLORER_DIRECTIVE,
    build_explorer_launch_prompt,
)


def test_explorer_launch_prompt_uses_role_directive():
    prompt = build_explorer_launch_prompt()
    assert EXPLORER_DIRECTIVE in prompt
    assert "submit_exploration_result" in prompt


def test_explorer_launch_prompt_takes_no_arguments():
    sig = inspect.signature(build_explorer_launch_prompt)
    assert list(sig.parameters) == []
