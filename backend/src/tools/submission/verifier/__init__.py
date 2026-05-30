"""Transitional re-export shim — the verifier profile/tools were removed.

The ``verifier`` submission package was deleted (Step 1 of the reducers
redesign, WS3). WF-B scenario files still import the old names; this shim
aliases them to the reducer tools so those imports stay import-safe until the
WF-B vocab pass reworks the verifier scenarios, at which point this module is
deleted.
"""

from tools.submission.reducer import (
    submit_reduction_failure as submit_verification_failure,
    submit_reduction_success as submit_verification_success,
)

__all__ = [
    "submit_verification_failure",
    "submit_verification_success",
]
