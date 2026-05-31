"""Package for the `submit_generator_outcome` tool."""

from . import submit_generator_outcome as _impl

submit_generator_outcome = _impl.submit_generator_outcome
SubmitGeneratorOutcomeInput = _impl.SubmitGeneratorOutcomeInput

__all__ = ["SubmitGeneratorOutcomeInput", "submit_generator_outcome"]
