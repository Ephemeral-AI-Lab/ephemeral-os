"""Description factory for submit_root_outcome."""

from __future__ import annotations


def get_submit_root_outcome_description() -> str:
    return """\
Terminate the root request with SUCCESS or FAILED.

## Inputs
- `status`: `"success"` when the user request is complete, or `"failed"` when
  it cannot be completed.
- `outcome`: concise user-facing final result. Include concrete verification or
  the concrete blocker.

## Behavior
- Records the root task outcome.
- Finishes the request so the caller can read the request result.\
"""


__all__ = ["get_submit_root_outcome_description"]
