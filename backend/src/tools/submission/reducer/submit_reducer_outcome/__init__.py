"""Package for the `submit_reducer_outcome` tool."""

from . import submit_reducer_outcome as _impl

submit_reducer_outcome = _impl.submit_reducer_outcome
SubmitReducerOutcomeInput = _impl.SubmitReducerOutcomeInput

__all__ = ["SubmitReducerOutcomeInput", "submit_reducer_outcome"]
