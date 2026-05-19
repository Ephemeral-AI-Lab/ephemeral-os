"""Description factory for submit_exploration_result."""

from __future__ import annotations

from tools._names import RUN_SUBAGENT_TOOL_NAME


def get_submit_exploration_result_description() -> str:
    return f"""\
Terminate as an explorer subagent with your read-only findings.

Call this when:
- You've completed the investigation you were spawned to do.
- You can present your findings with verifiable references (paths,
  line numbers, command outputs).

Inputs:
- `summary`: 1–3 sentence recap answering your original question
  directly.
- `findings`: bullet list of concrete observations.
- `references`: list of citable evidence (e.g.,
  `path/to/file.py:42`, `git log` excerpts) that backs each finding.

Behavior:
- Your summary is returned to the caller via `{RUN_SUBAGENT_TOOL_NAME}`'s result.

Style:
- You are read-only. Do not propose changes; describe what is.
- Cite evidence. A finding without a reference is just an assertion.\
"""
