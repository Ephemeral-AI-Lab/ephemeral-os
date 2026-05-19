"""Package for the `submit_advisor_feedback` tool.

`__init__.py` re-exports the impl module so that
`tools...submit_advisor_feedback` and `tools...submit_advisor_feedback.submit_advisor_feedback` resolve to the same module —
keeps monkeypatching `tools...submit_advisor_feedback.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import submit_advisor_feedback as _impl

sys.modules[__name__] = _impl
