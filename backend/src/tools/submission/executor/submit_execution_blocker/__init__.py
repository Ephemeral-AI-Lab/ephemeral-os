"""Package for the `submit_execution_blocker` tool."""

from . import submit_execution_blocker as _impl
from .submit_execution_blocker import SubmitExecutionBlockerInput

submit_execution_blocker = _impl.submit_execution_blocker

__all__ = [
    "SubmitExecutionBlockerInput",
    "submit_execution_blocker",
]
