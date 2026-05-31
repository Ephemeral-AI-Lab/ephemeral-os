"""Package for the `submit_exploration_result` tool."""

from . import submit_exploration_result as _impl

submit_exploration_result = _impl.submit_exploration_result
SubmitExplorationResultInput = _impl.SubmitExplorationResultInput

__all__ = ["SubmitExplorationResultInput", "submit_exploration_result"]
