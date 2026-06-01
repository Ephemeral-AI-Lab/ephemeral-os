"""Package for the `submit_root_outcome` tool."""

from . import submit_root_outcome as _impl

submit_root_outcome = _impl.submit_root_outcome
SubmitRootOutcomeInput = _impl.SubmitRootOutcomeInput

__all__ = ["SubmitRootOutcomeInput", "submit_root_outcome"]
