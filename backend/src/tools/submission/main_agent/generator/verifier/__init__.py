"""Verifier submission tools."""

from tools.submission.main_agent.generator.verifier.submit_verification_failure import (
    SubmitVerificationFailureInput,
    submit_verification_failure,
)
from tools.submission.main_agent.generator.verifier.submit_verification_success import (
    SubmitVerificationSuccessInput,
    submit_verification_success,
)

__all__ = [
    "SubmitVerificationFailureInput",
    "SubmitVerificationSuccessInput",
    "submit_verification_failure",
    "submit_verification_success",
]
