"""Transitional re-export shim — evaluator tools were renamed to reducer.

The ``evaluator`` submission package was renamed to ``reducer`` (Step 1 of the
reducers redesign). WF-B scenario files still import the old names; this shim
keeps them import-safe until the WF-B vocab pass renames those imports, at
which point this module is deleted.
"""

from tools.submission.reducer import (
    submit_reduction_failure as submit_evaluation_failure,
    submit_reduction_success as submit_evaluation_success,
)

__all__ = [
    "submit_evaluation_failure",
    "submit_evaluation_success",
]
